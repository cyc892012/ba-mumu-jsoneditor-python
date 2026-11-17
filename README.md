# MuMu 摸点小助手（Python 版）

这是 MuMu 模拟器《蔚蓝档案》键位方案编辑工具的 Python / PyQt5 重写版，源自作者的 C# 版本。C# 项目：[ba-mumu-jsoneditor](https://github.com/cyc892012/ba-mumu-jsoneditor)。

## 功能

- ADB 触控采集、MuMu 12 实例检测、坐标去重与批量写入。
- 鼠标坐标回溯、快捷键操作、键位预设注入、区域清空和全屏快速摸点。
- 超宽屏坐标转换、分辨率管理、区服包名分类与路径重新定位。
- 模拟器键位同步、文件变更监测、索引备份、日志查看及撤销 / 重做。
- 内置「基础键位」「爬塔键位」两套预设：首次启动自动放入 `data/`，不覆盖同名文件，用户删除后不会再次添加。

详细操作步骤见 [使用说明](docs/使用说明.md)。

## 坐标精度

MuMu 的落点换算按 `round(rel × W) × (W - 1) / W` 处理，其中 `rel` 是归一化坐标，`W` 是对应方向的屏幕尺寸。此换算用于匹配模拟器的取整行为。

## 运行环境

- Windows 10 或更新版本，64 位。
- Python 3.10 或更新版本；本次验证使用 Python 3.13.14，依赖版本以 `requirements.txt` 为准。
- MuMu 模拟器 12；未使用模拟器联动功能时可独立编辑键位 JSON。

## 运行

在项目目录安装依赖并启动：

```powershell
python -m pip install -r requirements.txt
python main.py
```

首次启动时，程序会在应用数据目录中生成默认 `config.json`。JSON Schema 校验使用可选依赖 `jsonschema`；未安装时仍会进行 JSON 语法校验。

如需隔离配置，可将环境变量 `MOMO_DATA_DIR` 设为单独的目录；日志、预设和备份也会写入该目录。仓库不附带个人配置、预设或 ADB 可执行文件。

## 测试与打包

运行全量单元测试。既有布局断言使用与 `main.py` 相同的 9pt 字体，以下命令先初始化应用字体，再发现并运行全部测试：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:MOMO_DATA_DIR = Join-Path $env:TEMP ("momo-tests-" + [guid]::NewGuid())
@'
import sys
import unittest
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QFont
app = QApplication([])
app.setFont(QFont("Microsoft YaHei UI", 9))
suite = unittest.defaultTestLoader.discover("tests")
result = unittest.TextTestRunner(verbosity=2).run(suite)
sys.exit(not result.wasSuccessful())
'@ | python -B -
```

ADB 查找顺序：手动指定路径 → 运行中的 MuMu 进程 → 注册表中的 MuMu 安装位置 → 常见安装目录 → 系统 PATH → 程序内置的 adb。仓库不包含 adb 二进制；如需内置兜底，请在打包前把 Android SDK Platform-Tools 中的 `adb.exe`、`AdbWinApi.dll`、`AdbWinUsbApi.dll` 放入 `tools/adb/`（许可证说明见该目录的 `NOTICE.txt`）。

安装开发 / 打包依赖后生成 onedir 程序：

```powershell
python -m pip install -r requirements-dev.txt
pyinstaller 摸点小助手.spec
```

## 目录结构

```text
adb/                    ADB 通信与触控事件解析
controls/               自定义控件
core/                   JSON、坐标、配置和备份逻辑
infra/                  日志、文件监测与模拟器同步
input/                  键盘和鼠标输入处理
ui/                     主界面与功能窗口
ui/assets/              界面图片、图标和 SVG
utils/                  通用工具
presets/                内置基础键位预设
tools/adb/              内置 adb 兜底说明（二进制需自行放入）
tests/                  单元测试和测试数据
main.py                 程序入口
摸点小助手.spec          PyInstaller onedir 配置
```

## 问题反馈

- 推荐在本仓库提交 [Issue](https://github.com/cyc892012/ba-mumu-jsoneditor-python/issues)，请附上模拟器分辨率、复现步骤和相关日志。
- 也可以发送邮件至 625161608@qq.com。

## 许可证与版权声明

本项目代码以 MIT 许可证发布，详见 [LICENSE](LICENSE)。第三方美术资源和来源尚待确认的图标不包含在 MIT 授权范围内，详见 [NOTICE.md](NOTICE.md)。

所安装的第三方 Python 库另受各自许可证约束，PyQt5 使用 GPLv3 或商业授权；本项目的 MIT 许可证不替代这些依赖的许可条款。
