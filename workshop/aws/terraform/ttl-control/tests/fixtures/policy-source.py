"""Autopilot fixture only; no live runner permissions."""
def identity(sts):
    return sts.get_caller_identity()
