# 文言实词限时训练

这是一个本地浏览器答题系统：本地服务提供学生答题页和管理员后台，题库与答题数据保存在用户自己的设备上。

## 运行

- 源码版：`python tools/run_server.py --port 8000`，然后打开 <http://127.0.0.1:8000>。
- Windows 免 Python 版：解压后双击 `文言实词限时训练.exe`。

本公开源码和发布资产不包含题库、审查记录或历史发布目录。请将自己的 `data/` 放在应用目录旁边，程序会继续使用现有本地数据。

## 检查更新

程序启动后会在后台查询 `YYiChen/wenyan-word-training` 的稳定 GitHub Release；只有进入管理员后台且存在更高版本时才提示。右上角“检查更新”可手动检查。

确认更新后，程序会下载并校验代码包，再由独立更新助手自动重启。更新只覆盖清单中的程序文件，不触碰应用旁的 `data/` 或 `%LOCALAPPDATA%/WenyanQuiz/` 中的排行榜、答题记录和管理员配置。源码目录有未提交修改时会跳过自动替换。

## 发布

使用 `python tools/build_release.py --output <目录>` 生成不含题库的源码包、Windows 包和 `SHA256SUMS.txt`。发布前请检查 ZIP 条目和校验和，并按 SemVer 创建稳定 Release。
