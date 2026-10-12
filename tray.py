"""The icon in the notification area, where the window goes when it is closed."""

import ctypes
from ctypes import wintypes
import queue
import threading

# Our own handles to the libraries: the prototypes below are not for everyone
user32 = ctypes.WinDLL("user32")
shell32 = ctypes.WinDLL("shell32")
kernel32 = ctypes.WinDLL("kernel32")

WINDOW = "HomePodCastTray"  # class of the hidden window that owns the icon

WM_DESTROY, WM_CLOSE, WM_ENDSESSION = 0x0002, 0x0010, 0x0016
WM_LBUTTONUP, WM_RBUTTONUP = 0x0202, 0x0205
WM_TRAY = 0x8001  # from the icon
WM_SHOW = 0x8002  # from a second copy of the program
NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 1, 2, 4, 16
NIIF_USER = 4
TPM_RIGHTBUTTON, TPM_RETURNCMD = 0x0002, 0x0100
IDI_APPLICATION = 32512
ASFW_ANY = 0xFFFFFFFF

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD), ("hWnd", wintypes.HWND), ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT), ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON), ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD), ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256), ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64), ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", wintypes.HICON),
    ]


def declare(function, result, *arguments):
    function.restype, function.argtypes = result, arguments


# Handles and message parameters are 64 bits wide; undeclared they would be cut to 32
declare(kernel32.GetModuleHandleW, wintypes.HMODULE, wintypes.LPCWSTR)
declare(user32.RegisterClassW, wintypes.ATOM, ctypes.POINTER(WNDCLASS))
declare(
    user32.CreateWindowExW, wintypes.HWND, wintypes.DWORD, wintypes.LPCWSTR,
    wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
)
declare(
    user32.DefWindowProcW, LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
    wintypes.LPARAM,
)
declare(user32.DestroyWindow, wintypes.BOOL, wintypes.HWND)
declare(user32.PostQuitMessage, None, ctypes.c_int)
declare(
    user32.GetMessageW, wintypes.BOOL, ctypes.POINTER(wintypes.MSG), wintypes.HWND,
    wintypes.UINT, wintypes.UINT,
)
declare(user32.DispatchMessageW, LRESULT, ctypes.POINTER(wintypes.MSG))
declare(
    user32.PostMessageW, wintypes.BOOL, wintypes.HWND, wintypes.UINT, wintypes.WPARAM,
    wintypes.LPARAM,
)
declare(user32.RegisterWindowMessageW, wintypes.UINT, wintypes.LPCWSTR)
declare(user32.FindWindowW, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR)
declare(user32.SetForegroundWindow, wintypes.BOOL, wintypes.HWND)
declare(user32.AllowSetForegroundWindow, wintypes.BOOL, wintypes.DWORD)
declare(user32.GetCursorPos, wintypes.BOOL, ctypes.POINTER(wintypes.POINT))
declare(user32.LoadIconW, wintypes.HICON, wintypes.HINSTANCE, wintypes.LPVOID)
declare(user32.CreatePopupMenu, wintypes.HMENU)
declare(
    user32.AppendMenuW, wintypes.BOOL, wintypes.HMENU, wintypes.UINT, ctypes.c_size_t,
    wintypes.LPCWSTR,
)
declare(
    user32.TrackPopupMenu, wintypes.BOOL, wintypes.HMENU, wintypes.UINT, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.LPVOID,
)
declare(user32.DestroyMenu, wintypes.BOOL, wintypes.HMENU)
declare(
    shell32.ExtractIconExW, wintypes.UINT, wintypes.LPCWSTR, ctypes.c_int,
    ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.HICON), wintypes.UINT,
)
declare(
    shell32.Shell_NotifyIconW, wintypes.BOOL, wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATA)
)


def show_running() -> bool:
    """Bring up the window of a copy that already runs; False if there is none."""
    hwnd = user32.FindWindowW(WINDOW, None)
    if not hwnd:
        return False
    user32.AllowSetForegroundWindow(ASFW_ANY)  # it may come to the front in our place
    user32.PostMessageW(hwnd, WM_SHOW, 0, 0)
    return True


class Tray(threading.Thread):
    """An icon in the notification area, with a message loop of its own.

    What the user asks for there is put in `clicks` as "show" or "quit", for the
    owner to collect in its own thread. `ending` is called, in this thread, when
    Windows is about to end the session.
    """

    def __init__(self, tip: str, icon_path: str, ending):
        super().__init__(daemon=True)
        self.tip = tip
        self.icon_path = icon_path
        self.ending = ending
        self.labels = ("Show", "Quit")  # of the menu
        self.clicks = queue.SimpleQueue()
        self.hwnd = None
        self.icon = NOTIFYICONDATA()
        self._handler = WNDPROC(self._handle)  # Windows keeps calling it: must stay alive
        self._restarted = user32.RegisterWindowMessageW("TaskbarCreated")
        self._ready = threading.Event()
        self.start()
        self._ready.wait()

    def run(self):
        instance = kernel32.GetModuleHandleW(None)
        user32.RegisterClassW(
            WNDCLASS(lpfnWndProc=self._handler, hInstance=instance, lpszClassName=WINDOW)
        )
        # Never shown. Not a message-only window: those are not told when Windows
        # ends the session or Explorer restarts, and FindWindow skips them.
        self.hwnd = user32.CreateWindowExW(
            0, WINDOW, None, 0, 0, 0, 0, 0, None, None, instance, None
        )
        image = wintypes.HICON()
        shell32.ExtractIconExW(self.icon_path, 0, None, image, 1)
        self.icon = NOTIFYICONDATA(
            cbSize=ctypes.sizeof(NOTIFYICONDATA), hWnd=self.hwnd, uID=1,
            uFlags=NIF_MESSAGE | NIF_ICON | NIF_TIP, uCallbackMessage=WM_TRAY,
            hIcon=image.value or user32.LoadIconW(None, IDI_APPLICATION), szTip=self.tip,
        )
        shell32.Shell_NotifyIconW(NIM_ADD, self.icon)
        self._ready.set()

        message = wintypes.MSG()
        while user32.GetMessageW(message, None, 0, 0) > 0:
            user32.DispatchMessageW(message)

    def _handle(self, hwnd, message, wparam, lparam):
        if message == WM_SHOW or (message == WM_TRAY and lparam == WM_LBUTTONUP):
            self.clicks.put("show")
        elif message == WM_TRAY and lparam == WM_RBUTTONUP:
            self._menu()
        elif message == self._restarted:
            shell32.Shell_NotifyIconW(NIM_ADD, self.icon)  # Explorer lost it
        elif message == WM_ENDSESSION and wparam:
            self.ending()
        elif message == WM_DESTROY:
            shell32.Shell_NotifyIconW(NIM_DELETE, self.icon)
            user32.PostQuitMessage(0)
        else:
            return user32.DefWindowProcW(hwnd, message, wparam, lparam)
        return 0

    def _menu(self):
        menu = user32.CreatePopupMenu()
        for number, label in enumerate(self.labels, 1):
            user32.AppendMenuW(menu, 0, number, label)
        at = wintypes.POINT()
        user32.GetCursorPos(at)
        # A menu whose window is not in front stays open when clicked away from
        user32.SetForegroundWindow(self.hwnd)
        chosen = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD, at.x, at.y, 0, self.hwnd, None
        )
        user32.DestroyMenu(menu)
        if chosen:
            self.clicks.put(("show", "quit")[chosen - 1])

    def notify(self, title: str, text: str):
        """Show a notification that comes from the icon."""
        shell32.Shell_NotifyIconW(
            NIM_MODIFY,
            NOTIFYICONDATA(
                cbSize=ctypes.sizeof(NOTIFYICONDATA), hWnd=self.hwnd, uID=1,
                uFlags=NIF_INFO, szInfo=text, szInfoTitle=title, dwInfoFlags=NIIF_USER,
            ),
        )

    def close(self):
        """Remove the icon."""
        user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)
        self.join(1)
