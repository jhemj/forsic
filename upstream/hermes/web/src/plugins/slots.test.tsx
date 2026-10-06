import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, expect, it } from 'vitest';
import { PluginSlot, registerSlot, unregisterPluginSlots } from './slots';

afterEach(() => unregisterPluginSlots('test-brand'));

it('replaces default branding and navigation instead of leaving hidden duplicate controls', () => {
  for (const name of ['header-left', 'sidebar']) {
    const fallback = <span>upstream default</span>;
    expect(renderToStaticMarkup(<PluginSlot name={name} fallback={fallback} />)).toContain('upstream default');
    registerSlot('test-brand', name, () => <span>product content</span>);
    const html = renderToStaticMarkup(<PluginSlot name={name} fallback={fallback} />);
    expect(html).toContain('product content');
    expect(html).not.toContain('upstream default');
  }
});

it('restores the default surface after the replacement plugin is removed', () => {
  registerSlot('test-brand', 'sidebar', () => <span>product content</span>);
  unregisterPluginSlots('test-brand');
  expect(renderToStaticMarkup(<PluginSlot name="sidebar" fallback={<span>default navigation</span>} />))
    .toContain('default navigation');
});
