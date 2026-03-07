def parse_facility_level(provider) -> int:
    """Extract numeric facility level from Provider — handles enum, string, or None."""
    raw = getattr(provider, "facility_level", None)
    if raw is None:
        return 4
    level_str = raw.value if hasattr(raw, "value") else str(raw)
    try:
        return int(level_str.replace("LEVEL_", ""))
    except (ValueError, AttributeError):
        return 4
