#!/usr/bin/env python3
import sys
import re
from pathlib import Path

def bump_version(new_version: str):
    root_dir = Path(__file__).parent.parent
    
    # 定义需要更新的文件及对应的正则匹配规则
    files_to_update = [
        {
            "path": "app/version.py",
            "pattern": r'(__version__\s*=\s*")[^"]+(")',
            "repl": rf'\g<1>{new_version}\g<2>'
        },
        {
            "path": "pyproject.toml",
            "pattern": r'(version\s*=\s*")[^"]+(")',
            "repl": rf'\g<1>{new_version}\g<2>'
        },
        {
            "path": "openwrt/app-meta-wechat-bridge/Makefile",
            "pattern": r'(PKG_VERSION:=)[^\s]+',
            "repl": rf'\g<1>{new_version}'
        },
        {
            "path": "openwrt/luci-app-wechat-bridge/Makefile",
            "pattern": r'(PKG_VERSION:=)[^\s]+',
            "repl": rf'\g<1>{new_version}'
        }
    ]

    for item in files_to_update:
        file_path = root_dir / item["path"]
        if not file_path.exists():
            print(f"⚠️ 文件不存在: {file_path.name}")
            continue
            
        content = file_path.read_text(encoding="utf-8")
        new_content, count = re.subn(item["pattern"], item["repl"], content)
        
        if count > 0:
            file_path.write_text(new_content, encoding="utf-8")
            print(f"✅ 更新成功: {item['path']} -> {new_version}")
        else:
            print(f"⚠️ 未找到匹配项: {item['path']}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("用法: python scripts/bump_version.py <新版本号>")
        print("示例: python scripts/bump_version.py 1.3.0")
        sys.exit(1)
        
    bump_version(sys.argv[1])
