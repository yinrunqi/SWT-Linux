cd C:\codes\sw\SolidWorksTutorial\LLMPlanner
venv\Scripts\activate.bat
set OPENAI_API_KEY=sk-YdzOBanGq5rkDotCp3ziSTmLKq0i1l4eoZoIyNHzS6gkplVl
set OPENAI_BASE_URL=https://api.juheai.top/v1

# 交互模式
# python -m planner.main

# 单次执行（dry-run 模拟）
python -m planner.main --dry-run "创建一个 100mm x 50mm x 30mm 的长方体"