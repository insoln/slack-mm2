from __future__ import annotations

def parse_slack_ts(slack_id) -> float:
    """
    Convert a Slack message/reaction ID timestamp to float.
    - Accepts pure timestamps (str/number) or composite IDs like "<ts>_<name>_<user>".
    - Returns float('inf') on failure to ensure such items sort last.
    """
    try:
        if slack_id is None:
            return float("inf")
        s = slack_id
        # If composite like "<ts>_...", take the first part
        if isinstance(s, str) and "_" in s:
            s = s.split("_", 1)[0]
        return float(s)
    except Exception:
        return float("inf")
