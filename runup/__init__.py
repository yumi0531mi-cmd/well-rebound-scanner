"""Runup scanner package skeleton (Step 01).

중앙 설정은 config.py의 RUNUP_CONFIG 한 곳에서만 관리한다. 이 패키지는 값을
복제하지 않으며, 전략 계산·네트워크·UI 구현은 이후 지정 단계에서만 추가한다.
"""

__all__ = ["get_runup_config", "get_runup_profile_name"]


def get_runup_profile_name():
    import config

    return config.RUNUP_PROFILE_NAME


def get_runup_config():
    import config

    return config.RUNUP_CONFIG
