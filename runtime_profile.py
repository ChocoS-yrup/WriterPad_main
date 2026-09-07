import os


def profile_name():
    return os.environ.get("ANTIGRAVITY_PROFILE", "").strip()


def root_dir(default_root):
    override = os.environ.get("ANTIGRAVITY_ROOT_DIR", "").strip()
    return os.path.abspath(override) if override else os.path.abspath(default_root)


def app_data_dir():
    override = os.environ.get("ANTIGRAVITY_APP_DATA_DIR", "").strip()
    if override:
        return os.path.abspath(override)
    return os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "AntigravityWriter",
    )


def instance_key(default_key):
    return os.environ.get("ANTIGRAVITY_INSTANCE_KEY", "").strip() or default_key


def pid_file_path():
    return os.environ.get("ANTIGRAVITY_PID_FILE", "").strip()


def forced_project_id():
    return os.environ.get("ANTIGRAVITY_SYNC_PROJECT_ID", "").strip()


def offline_marker_path():
    return os.environ.get("ANTIGRAVITY_SYNC_OFFLINE_FILE", "").strip()


def is_forced_offline():
    marker = offline_marker_path()
    return bool(marker and os.path.exists(marker))


# --- the machine-wide credential lock -----------------------------------------
#
# Built here, and only here, so that the application and the preflight cannot
# drift into naming two different objects and each conclude it is alone.

_CREDENTIAL_LEASE_PREFIX = "Global\\AntigravityWriterSupabaseAuth"


def profile_id():
    """What makes one profile's stored session a different stored session.

    security_manager.service_name() appends the profile to the keyring service,
    so two profiles never read or retire each other's token. Case and
    composition are folded out because the Windows credential store resolves a
    target name case-insensitively: two spellings that reach one stored session
    have to reach one lock. Folding can only ever join two profiles under a
    single lock, never split one across two, so it errs towards blocking.
    """
    import unicodedata

    return unicodedata.normalize("NFC", profile_name()).casefold()


def user_sid():
    """The string SID of the account this process runs as.

    Read from the process token rather than from the environment. USERNAME is
    writable by whoever starts us, and a lock scope that the environment can
    choose is not a scope.

    Raises when it cannot be read. There is no answer to fall back on: a
    placeholder would put the processes that failed to read it in a different
    room from the ones that succeeded, each certain it was alone.
    """
    import ctypes
    from ctypes import wintypes

    TOKEN_QUERY = 0x0008
    TOKEN_USER = 1

    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetCurrentProcess.argtypes = []
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.LocalFree.restype = wintypes.HANDLE
    kernel32.LocalFree.argtypes = [wintypes.HANDLE]
    advapi32.OpenProcessToken.restype = wintypes.BOOL
    advapi32.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)
    ]
    advapi32.GetTokenInformation.restype = wintypes.BOOL
    advapi32.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)
    ]

    token = wintypes.HANDLE()
    if not advapi32.OpenProcessToken(
        kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = wintypes.DWORD(0)
        advapi32.GetTokenInformation(
            token, TOKEN_USER, None, 0, ctypes.byref(size)
        )
        if not size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi32.GetTokenInformation(
            token, TOKEN_USER, buffer, size, ctypes.byref(size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        # TOKEN_USER is a SID_AND_ATTRIBUTES, whose first member is the PSID.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents
        text = wintypes.LPWSTR()
        if not advapi32.ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            value = text.value
        finally:
            kernel32.LocalFree(text)
        if not value:
            raise OSError("the account SID came back empty")
        return value
    finally:
        kernel32.CloseHandle(token)


def credential_lease_name():
    """The kernel object that names the right to exchange one stored session.

    Scoped to exactly the processes that share that session: this Windows
    account, this profile. Two profiles cannot retire each other's token, and
    two accounts do not share a credential store, so neither pair has anything
    to gain by waiting on the other -- a single fixed name only stopped work
    that was never in danger.

    Both parts reach the name as one digest. A raw profile name arrives from
    the environment: it can hold a backslash, which would silently name a
    different object, or run past the length a kernel object name allows. A
    digest can do neither, is the same on every run, and is safe to print.

    One namespace, and no falling back to another. A fallback would put the two
    holders in separate rooms, each certain it was alone. When the name cannot
    be built there is no lock to take, and that is a stop.
    """
    import hashlib

    scope = f"{user_sid()}\0{profile_id()}".encode("utf-8")
    return f"{_CREDENTIAL_LEASE_PREFIX}-{hashlib.sha256(scope).hexdigest()[:32]}"
