import sys
from pathlib import Path

# 云端(Streamlit Community Cloud)从 requirements.txt 直接安装依赖,
# 没有 `pip install -e .`,src/ 布局的包不会自动可导入;
# 本地 editable 安装场景下该条目与安装路径指向同一份代码,无副作用。
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from web_task_agent.streamlit_app import main

if __name__ == "__main__":
    main()
