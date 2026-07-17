import keyring

class SecurityManager:
    SERVICE_NAME = "Antigravity_WebNovelApp"

    @staticmethod
    def service_name():
        from runtime_profile import profile_name
        profile = profile_name()
        return f"{SecurityManager.SERVICE_NAME}_{profile}" if profile else SecurityManager.SERVICE_NAME
    
    @staticmethod
    def save_api_key(model_name: str, api_key: str):
        """
        API 키를 시스템 키체인에 안전하게 저장합니다.
        빈 문자열이나 None이 들어오면 해당 키를 삭제(또는 빈 값으로 처리)합니다.
        """
        if not api_key:
            try:
                keyring.delete_password(SecurityManager.service_name(), model_name)
            except keyring.errors.PasswordDeleteError:
                pass # 이미 없으면 무시
        else:
            keyring.set_password(SecurityManager.service_name(), model_name, api_key)
            
    @staticmethod
    def get_api_key(model_name: str) -> str:
        """
        저장된 API 키를 시스템 키체인에서 불러옵니다.
        저장된 값이 없으면 빈 문자열을 반환합니다.
        """
        key = keyring.get_password(SecurityManager.service_name(), model_name)
        return key if key else ""

    @staticmethod
    def save_supabase_session(access_token: str, refresh_token: str):
        SecurityManager.save_api_key("SupabaseAccessToken", access_token)
        SecurityManager.save_api_key("SupabaseRefreshToken", refresh_token)

    @staticmethod
    def get_supabase_session():
        return (
            SecurityManager.get_api_key("SupabaseAccessToken"),
            SecurityManager.get_api_key("SupabaseRefreshToken"),
        )

    @staticmethod
    def clear_supabase_session():
        SecurityManager.save_api_key("SupabaseAccessToken", "")
        SecurityManager.save_api_key("SupabaseRefreshToken", "")
