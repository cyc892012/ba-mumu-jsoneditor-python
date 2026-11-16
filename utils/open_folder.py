"""通过 Windows 资源管理器异步打开目录或选中文件。"""
import os
import subprocess


def open_folder(path, *, select=False):
    path = os.path.normpath(os.fspath(path))
    if select:
        # Explorer 的 /select, 后单独引用路径；空格、中文、括号均保留在路径内。
        # 不使用 shell，路径中的 & 等字符也不会被当成命令执行。
        if '"' in path:
            raise ValueError("文件路径不能包含双引号")
        subprocess.Popen(f'explorer /select,"{path}"', shell=False,
                         creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        os.startfile(path)
