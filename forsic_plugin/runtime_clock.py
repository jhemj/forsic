"""Server-owned investigation-time snapshot, separate from evidence timestamps."""
from datetime import datetime, timezone


def current_context():
    # astimezone() reads the server's effective timezone, including its UTC offset.
    # This is a snapshot at a native context boundary, not a live evidence clock.
    now = datetime.now(timezone.utc).astimezone()
    return {
        'current_datetime': now.isoformat(timespec='seconds'),
        'timezone': now.tzname(),
        'basis': 'server_clock_at_context_refresh',
        'instruction': (
            'Use this dated snapshot as present investigation time, not a model training cutoff or event time. '
            'It refreshes on turn/resume, compression, and independent review; time can pass during a long turn. '
            'Evidence records may describe historical events. Preserve raw dates and timezone uncertainty; '
            'do not assume the server timezone applies to evidence or fill a missing year from the current year. A future or skewed evidence timestamp is an '
            'anomaly to assess, not a date to silently correct. Filesystem ctime alone does not establish deletion.'
        ),
    }
