"""The requirement owner's pure interest-selection contract (R01)."""

from .models import InterestTag, TravelPace, TravelPreferences


def interest_selection_error(preferences: TravelPreferences) -> str | None:
    selected = preferences.interests
    if selected and preferences.agent_decides_interests:
        return "interest_selection_conflict"
    if not selected:
        return None if preferences.agent_decides_interests else "interests_missing"
    if not 1 <= len(selected) <= len(InterestTag) or len(set(selected)) != len(selected):
        return "interests_invalid"
    if any(not isinstance(item, InterestTag) for item in selected):
        return "interests_invalid"
    return None


def pace_selection_error(preferences: TravelPreferences) -> str | None:
    if preferences.pace is None:
        return None if preferences.agent_decides_pace else "pace_missing"
    if preferences.agent_decides_pace:
        return "pace_selection_conflict"
    return None if isinstance(preferences.pace, TravelPace) else "pace_invalid"
