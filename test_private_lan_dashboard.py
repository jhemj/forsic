"""Offline transport tests for explicitly trusted private-LAN dashboard access."""
import os
import asyncio
from contextlib import asynccontextmanager
import socket
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock, Mock

from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


class PrivateLanDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.environment = patch.dict(os.environ, {'HERMES_HOME': self.temp.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        from hermes_cli import web_server as server, web_server_chat as chat
        self.server, self.chat = server, chat
        self.app = FastAPI()
        self.patch(server, 'app', self.app)
        self.app.state.bound_host = '192.168.10.4'
        self.app.state.trusted_public_hosts = frozenset()
        self.app.state.auth_required = False
        self.app.state.trusted_private_lan_no_login = True
        self.app.middleware('http')(server.auth_middleware)
        self.app.middleware('http')(server.host_header_middleware)

        @self.app.get('/api/private-probe')
        def probe():
            return {'ok': True}

        @self.app.websocket('/ws')
        async def socket(ws: WebSocket):
            if not chat._ws_request_is_allowed(ws) or not chat._ws_auth_ok(ws):
                await ws.close(code=1008)
                return
            await ws.accept()
            await ws.send_json({'ok': True})
            await ws.close()

        dist = Path(self.temp.name) / 'dist'
        dist.mkdir()
        (dist / 'index.html').write_text('<html><head></head><body>UI</body></html>')
        self.patch(server, 'WEB_DIST', dist)
        from hermes_cli import web_server_dashboard as dashboard
        self.patch(dashboard, '_render_active_theme_bootstrap_css', lambda: '')
        from hermes_cli import web_server_profiles
        self.patch(web_server_profiles, 'serving_profile_name', lambda: 'default')
        dashboard.mount_spa(self.app)

    def patch(self, obj, name, value):
        patcher = patch.object(obj, name, value)
        patcher.start()
        self.addCleanup(patcher.stop)

    def client(self, peer='192.168.10.8'):
        return TestClient(self.app, base_url='http://192.168.10.4:8860', client=(peer, 43210))

    def test_private_browser_gets_native_bootstrap_and_api_still_requires_token(self):
        client = self.client()
        response = client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn('window.__HERMES_SESSION_TOKEN__=', response.text)
        self.assertIn('window.__HERMES_AUTH_REQUIRED__=false', response.text)
        self.assertEqual(client.get('/api/private-probe').status_code, 401)
        headers = {self.server._SESSION_HEADER_NAME: self.server._SESSION_TOKEN}
        self.assertEqual(client.get('/api/private-probe', headers=headers).status_code, 200)

    def test_public_missing_and_reserved_peers_cannot_read_token_or_forge_forwarding(self):
        for peer in ('8.8.8.8', '', '100.64.0.1', '169.254.1.1', '192.0.2.1'):
            with self.subTest(peer=peer):
                response = self.client(peer).get('/', headers={
                    'X-Forwarded-For': '192.168.10.8', 'X-Forwarded-Host': '192.168.10.4:8860'})
                self.assertEqual(response.status_code, 403)
                self.assertNotIn('__HERMES_SESSION_TOKEN__', response.text)

    def test_host_and_origin_must_match_actual_authority(self):
        for headers in ({'Host': 'example.com'}, {'Host': '8.8.8.8'},
                        {'Origin': 'http://evil.example'}, {'Origin': 'null'},
                        {'Origin': 'http://192.168.10.4:9999'}, {'Origin': 'http://['}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client().get('/', headers=headers).status_code, 403)
        self.assertEqual(self.client().get('/', headers={'Origin': 'http://192.168.10.4:8860'}).status_code, 200)

    def test_websocket_preserves_peer_origin_and_native_token_guards(self):
        token = self.server._SESSION_TOKEN
        for peer, origin, credential in (
            ('8.8.8.8', 'http://192.168.10.4:8860', token),
            ('192.168.10.8', 'http://evil.example', token),
            ('192.168.10.8', 'null', token),
            ('192.168.10.8', 'http://192.168.10.4:8860', 'wrong'),
        ):
            with self.subTest(peer=peer, origin=origin, valid_token=credential == token):
                with self.assertRaises(WebSocketDisconnect):
                    with self.client(peer).websocket_connect('ws://192.168.10.4:8860/ws?token='+credential, headers={
                        'Origin': origin, 'X-Forwarded-For': '192.168.10.8'}):
                        pass
        with self.client().websocket_connect('ws://192.168.10.4:8860/ws?token='+token,
                headers={'Origin': 'http://192.168.10.4:8860'}) as ws:
            self.assertEqual(ws.receive_json(), {'ok': True})
        with self.assertRaises(WebSocketDisconnect):
            with self.client().websocket_connect('ws://8.8.8.8:8860/ws?token='+token,
                    headers={'Origin': 'http://8.8.8.8:8860',
                             'X-Forwarded-Host': '192.168.10.4:8860'}):
                pass

    def test_configuration_is_explicit_and_default_public_auth_is_unchanged(self):
        server = self.server
        for host in ('192.168.10.4', 'fd12::4', '127.0.0.1'):
            with patch.object(server, 'load_config', return_value={'dashboard': {'trusted_private_lan_no_login': True}}), \
                 patch.object(server, '_dashboard_public_hosts', return_value=frozenset()):
                server._configure_auth_gate(host, False, None, None)
                self.assertFalse(self.app.state.auth_required)
        for host, config, hosts in (
            ('0.0.0.0', True, frozenset()), ('::', True, frozenset()),
            ('8.8.8.8', True, frozenset()), ('192.168.10.4', 'true', frozenset()),
            ('192.168.10.4', True, frozenset({'evil.example'})),
        ):
            with self.subTest(host=host, config=config, hosts=hosts):
                dashboard = {'trusted_private_lan_no_login': config}
                if hosts:
                    dashboard['public_url'] = 'https://evil.example'
                with patch.object(server, 'load_config', return_value={'dashboard': dashboard}), \
                     patch.object(server, '_dashboard_public_hosts', return_value=hosts):
                    with self.assertRaises(SystemExit):
                        server._configure_auth_gate(host, False, None, None)
        with patch.object(server, 'load_config', return_value={}), \
             patch.object(server, '_dashboard_public_hosts', return_value=frozenset()), \
             patch('hermes_cli.dashboard_auth.list_providers', return_value=[]):
            with self.assertRaises(SystemExit):
                server._configure_auth_gate('192.168.10.4', True, None, None)
            self.assertTrue(self.app.state.auth_required)
            self.assertFalse(self.app.state.trusted_private_lan_no_login)

    def test_uvicorn_does_not_trust_forwarded_headers_in_lan_mode(self):
        with patch.object(self.server, 'load_config', return_value={}):
            config, server = self.server._build_uvicorn_server('192.168.10.4', 8860)
        self.assertFalse(config.proxy_headers)

    def test_explicit_second_listener_preserves_loopback_and_validates_configuration(self):
        config = {'dashboard': {'trusted_private_lan_no_login': True, 'private_lan_host': '192.168.10.4'}}
        with patch.object(self.server, 'load_config', return_value=config), \
             patch.object(self.server, '_dashboard_public_hosts', return_value=frozenset()):
            self.server._configure_auth_gate('127.0.0.1', False, None, None)
        self.assertFalse(self.app.state.auth_required)
        self.assertEqual(self.app.state.private_lan_host, '192.168.10.4')
        self.assertEqual(self.app.state.private_lan_listener_hosts, frozenset({'127.0.0.1', '192.168.10.4'}))
        for host, flag in [('0.0.0.0', True), ('::', True), ('8.8.8.8', True),
                           ('127.0.0.2', True), ('192.168.10.4', False), ('example.com', True)]:
            with self.subTest(host=host, flag=flag), \
                 patch.object(self.server, 'load_config', return_value={'dashboard': {
                     'trusted_private_lan_no_login': flag, 'private_lan_host': host}}), \
                 patch.object(self.server, '_dashboard_public_hosts', return_value=frozenset()):
                with self.assertRaises(SystemExit):
                    self.server._configure_auth_gate('127.0.0.1', False, None, None)

    def test_dual_listener_http_ws_use_actual_socket_host_and_port(self):
        self.app.state.bound_host = '127.0.0.1'
        self.app.state.private_lan_listener_hosts = frozenset({'127.0.0.1', '192.168.10.4'})
        token = self.server._SESSION_TOKEN
        for host, peer in [('127.0.0.1', '127.0.0.1'), ('192.168.10.4', '192.168.10.8')]:
            with self.subTest(host=host):
                client = TestClient(self.app, base_url=f'http://{host}:8860', client=(peer, 4321))
                self.assertEqual(client.get('/').status_code, 200)
                with client.websocket_connect(f'ws://{host}:8860/ws?token='+token,
                        headers={'Origin': f'http://{host}:8860'}) as ws:
                    self.assertEqual(ws.receive_json(), {'ok': True})
                for authority in ('example.com:8860', host+':9999',
                                  ('192.168.10.4' if host == '127.0.0.1' else '127.0.0.1')+':8860'):
                    self.assertEqual(client.get('/', headers={'Host': authority,
                        'X-Forwarded-Host': host+':8860'}).status_code, 403)
                    with self.assertRaises(WebSocketDisconnect):
                        with client.websocket_connect(f'ws://{host}:8860/ws?token='+token,
                                headers={'Host': authority, 'Origin': 'http://'+authority}):
                            pass

    def test_failed_second_bind_closes_both_sockets(self):
        first, second = Mock(), Mock()
        first.family = second.family = socket.AF_INET
        first.getsockname.return_value = ('127.0.0.1', 43210)
        second.bind.side_effect = OSError('synthetic unavailable interface')
        with patch.object(self.server.socket, 'socket', side_effect=[first, second]):
            with self.assertRaises(OSError):
                self.server._bind_private_lan_sockets('127.0.0.1', '192.168.10.4', 0)
        first.close.assert_called_once()
        second.close.assert_called_once()

    def test_startup_failure_closes_sockets_and_shuts_down_partial_server(self):
        first, second = Mock(), Mock()
        server = Mock(startup=AsyncMock(side_effect=RuntimeError('synthetic startup failure')),
                      shutdown=AsyncMock(), main_loop=AsyncMock())
        on_started = Mock()
        with patch.object(self.server, '_bind_private_lan_sockets', return_value=[first, second]):
            with self.assertRaises(RuntimeError):
                asyncio.run(self.server._serve_private_lan(server, '127.0.0.1', '192.168.10.4', 8860, on_started))
        first.close.assert_called_once()
        second.close.assert_called_once()
        server.shutdown.assert_awaited_once_with(sockets=[first, second])
        on_started.assert_not_called()

    def test_two_real_loopback_sockets_share_port_server_and_single_lifespan(self):
        # A second loopback alias stands in for a private NIC without touching
        # any machine LAN interface or the live dashboard port.
        import uvicorn
        events, sockets = [], []

        @asynccontextmanager
        async def lifespan(app):
            events.append('start')
            yield
            events.append('stop')

        app = FastAPI(lifespan=lifespan)

        @app.get('/')
        def probe():
            return {'synthetic': True}

        async def exercise():
            config = uvicorn.Config(app, log_level='error', proxy_headers=False, timeout_graceful_shutdown=1)
            config.load()
            server = uvicorn.Server(config)
            server.lifespan = config.lifespan_class(config)
            original = self.server._bind_private_lan_sockets

            def bind(*args):
                result = original(*args)
                sockets.extend(result)
                return result

            async def requests():
                try:
                    self.assertEqual(sockets[0].getsockname()[1], sockets[1].getsockname()[1])
                    port = sockets[0].getsockname()[1]
                    for host in ('127.0.0.1', '127.0.0.2'):
                        reader, writer = await asyncio.open_connection(host, port)
                        writer.write(f'GET / HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n'.encode())
                        await writer.drain()
                        response = await reader.read()
                        writer.close()
                        await writer.wait_closed()
                        self.assertIn(b'200 OK', response)
                        self.assertIn(b'"synthetic":true', response)
                finally:
                    server.should_exit = True

            task = None

            def started():
                nonlocal task
                task = asyncio.create_task(requests())

            with patch.object(self.server, '_bind_private_lan_sockets', side_effect=bind):
                await asyncio.wait_for(self.server._serve_private_lan(
                    server, '127.0.0.1', '127.0.0.2', 0, started), timeout=10)
            await task
            self.assertEqual(server.server_state.total_requests, 2)

        asyncio.run(exercise())
        self.assertEqual(events, ['start', 'stop'])
        self.assertTrue(all(sock.fileno() == -1 for sock in sockets))


if __name__ == '__main__':
    unittest.main()
