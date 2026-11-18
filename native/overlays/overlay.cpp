#include <windows.h>
#include <shlobj.h>
#include <bcrypt.h>
#include <string>
#include <cstdio>
#include <new>

static HMODULE module;
static LONG objects = 0;
static const CLSID ids[] = {
    {0xa22f0821,0x2b78,0x49ce,{0xb2,0x7c,0x10,0x41,0xd8,0x75,0xb0,0x11}},
    {0xa22f0822,0x2b78,0x49ce,{0xb2,0x7c,0x10,0x41,0xd8,0x75,0xb0,0x11}},
    {0xa22f0823,0x2b78,0x49ce,{0xb2,0x7c,0x10,0x41,0xd8,0x75,0xb0,0x11}}
};

static std::wstring keyFor(const wchar_t* path) {
    wchar_t full[4096];
    DWORD count = GetFullPathNameW(path, 4096, full, nullptr);
    if (!count || count >= 4096) return L"";
    CharLowerBuffW(full, count);
    BYTE digest[32];
    if (BCryptHash(BCRYPT_SHA256_ALG_HANDLE, nullptr, 0,
            reinterpret_cast<BYTE*>(full), count * sizeof(wchar_t), digest, 32) < 0) return L"";
    std::wstring result;
    for (BYTE byte : digest) {
        result += L"0123456789abcdef"[byte >> 4];
        result += L"0123456789abcdef"[byte & 15];
    }
    return result;
}

static void request(const wchar_t* path, const std::wstring& base, const std::wstring& key) {
    // One bounded local request per path; never call the network inside Explorer.
    size_t count = wcsnlen_s(path, 4096);
    if (!count || count >= 4096) return;
    HANDLE file = CreateFileW((base + L"\\requests\\" + key + L".txt").c_str(), GENERIC_WRITE,
        FILE_SHARE_READ | FILE_SHARE_DELETE, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return;
    DWORD written;
    WriteFile(file, path, static_cast<DWORD>(count * sizeof(wchar_t)), &written, nullptr);
    CloseHandle(file);
}

static int stateAt(const wchar_t* path, const std::wstring& base) {
    // UNC/network roots must not turn a shell icon callback into network I/O.
    if ((path[0] == L'\\' && path[1] == L'\\') || base.rfind(L"\\\\", 0) == 0) return 0;
    wchar_t roots[32768] = {};
    DWORD count = 0;
    HANDLE rootsFile = CreateFileW((base + L"\\roots.txt").c_str(), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (rootsFile == INVALID_HANDLE_VALUE) return 0;
    BOOL okay = ReadFile(rootsFile, roots, sizeof(roots) - sizeof(wchar_t), &count, nullptr);
    CloseHandle(rootsFile);
    if (!okay || count % 2) return 0;
    std::wstring all(roots, count / sizeof(wchar_t));
    bool allowed = false;
    size_t start = 0, length = wcsnlen_s(path, 4096);
    if (length >= 4096) return 0;
    while (start < all.size()) {
        size_t end = all.find(L'\n', start); if (end == std::wstring::npos) end = all.size();
        auto root = all.substr(start, end - start);
        if (!root.empty() && root.size() <= length && CompareStringOrdinal(root.c_str(), static_cast<int>(root.size()), path,
                static_cast<int>(root.size()), TRUE) == CSTR_EQUAL &&
                (root.size() == length || root.back() == L'\\' || path[root.size()] == L'\\')) { allowed = true; break; }
        start = end + 1;
    }
    if (!allowed) return 0;
    auto key = keyFor(path);
    if (key.empty()) return 0;
    char data[256] = {};
    DWORD read = 0;
    HANDLE file = CreateFileW((base + L"\\cache\\" + key + L".txt").c_str(), GENERIC_READ,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, nullptr, OPEN_EXISTING,
        FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file != INVALID_HANDLE_VALUE) {
        ReadFile(file, data, sizeof(data) - 1, &read, nullptr);
        CloseHandle(file);
    }
    char state = 0;
    unsigned long long expires = 0, modified = 0, size = 0;
    FILETIME now;
    GetSystemTimeAsFileTime(&now);
    ULARGE_INTEGER time; time.LowPart = now.dwLowDateTime; time.HighPart = now.dwHighDateTime;
    if (sscanf_s(data, "U1 %c %llu %llu %llu", &state, 1u, &expires, &modified, &size) != 4 || expires < time.QuadPart) {
        request(path, base, key); return 0;
    }
    WIN32_FILE_ATTRIBUTE_DATA attrs;
    if (!GetFileAttributesExW(path, GetFileExInfoStandard, &attrs) || (attrs.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) return 0;
    ULARGE_INTEGER actual; actual.LowPart = attrs.ftLastWriteTime.dwLowDateTime; actual.HighPart = attrs.ftLastWriteTime.dwHighDateTime;
    unsigned long long bytes = (static_cast<unsigned long long>(attrs.nFileSizeHigh) << 32) | attrs.nFileSizeLow;
    if (state != 'R' && (actual.QuadPart != modified || bytes != size)) {
        request(path, base, key); return 1;
    }
    return state == 'B' ? 1 : state == 'G' ? 2 : state == 'R' ? 3 : 0;
}

extern "C" __declspec(dllexport) int __stdcall OverlayStateAt(const wchar_t* path, const wchar_t* cache) {
    try { return path && cache ? stateAt(path, cache) : 0; } catch (...) { return 0; }
}

class Overlay final : public IShellIconOverlayIdentifier {
    LONG references = 1;
    int color;
public:
    explicit Overlay(int value) : color(value) { InterlockedIncrement(&objects); }
    ~Overlay() { InterlockedDecrement(&objects); }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER; *out = nullptr;
        if (iid == IID_IUnknown || iid == IID_IShellIconOverlayIdentifier) { *out = static_cast<IShellIconOverlayIdentifier*>(this); AddRef(); return S_OK; }
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&references); }
    ULONG STDMETHODCALLTYPE Release() override { LONG left = InterlockedDecrement(&references); if (!left) delete this; return left; }
    HRESULT STDMETHODCALLTYPE IsMemberOf(LPCWSTR path, DWORD) override {
        wchar_t local[MAX_PATH];
        if (!path || FAILED(SHGetFolderPathW(nullptr, CSIDL_LOCAL_APPDATA, nullptr, SHGFP_TYPE_CURRENT, local))) return S_FALSE;
        try { return stateAt(path, std::wstring(local) + L"\\UGSync\\overlays") == color ? S_OK : S_FALSE; }
        catch (...) { return S_FALSE; }
    }
    HRESULT STDMETHODCALLTYPE GetOverlayInfo(LPWSTR file, int length, int* index, DWORD* flags) override {
        if (!file || !index || !flags || length <= 0) return E_INVALIDARG;
        wchar_t path[4096]; DWORD size = GetModuleFileNameW(module, path, 4096);
        if (!size || size >= 4096) return E_FAIL;
        std::wstring name(path); name = name.substr(0, name.find_last_of(L"\\/") + 1);
        name += color == 1 ? L"blue.ico" : color == 2 ? L"green.ico" : L"red.ico";
        if (wcscpy_s(file, length, name.c_str())) return E_FAIL;
        *index = 0; *flags = ISIOI_ICONFILE | ISIOI_ICONINDEX; return S_OK;
    }
    HRESULT STDMETHODCALLTYPE GetPriority(int* priority) override { if (!priority) return E_POINTER; *priority = 0; return S_OK; }
};

class Factory final : public IClassFactory {
    LONG references = 1;
    int color;
public:
    explicit Factory(int value) : color(value) { InterlockedIncrement(&objects); }
    ~Factory() { InterlockedDecrement(&objects); }
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER; *out = nullptr;
        if (iid == IID_IUnknown || iid == IID_IClassFactory) { *out = static_cast<IClassFactory*>(this); AddRef(); return S_OK; }
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&references); }
    ULONG STDMETHODCALLTYPE Release() override { LONG left = InterlockedDecrement(&references); if (!left) delete this; return left; }
    HRESULT STDMETHODCALLTYPE CreateInstance(IUnknown* outer, REFIID iid, void** out) override {
        if (outer) return CLASS_E_NOAGGREGATION;
        auto item = new(std::nothrow) Overlay(color); if (!item) return E_OUTOFMEMORY;
        HRESULT result = item->QueryInterface(iid, out); item->Release(); return result;
    }
    HRESULT STDMETHODCALLTYPE LockServer(BOOL lock) override { if (lock) InterlockedIncrement(&objects); else InterlockedDecrement(&objects); return S_OK; }
};

STDAPI DllGetClassObject(REFCLSID clsid, REFIID iid, void** out) {
    if (!out) return E_POINTER; *out = nullptr;
    for (int i = 0; i < 3; ++i) if (clsid == ids[i]) {
        auto factory = new(std::nothrow) Factory(i + 1); if (!factory) return E_OUTOFMEMORY;
        HRESULT result = factory->QueryInterface(iid, out); factory->Release(); return result;
    }
    return CLASS_E_CLASSNOTAVAILABLE;
}
STDAPI DllCanUnloadNow() { return objects == 0 ? S_OK : S_FALSE; }
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) { module = instance; DisableThreadLibraryCalls(instance); } return TRUE;
}
