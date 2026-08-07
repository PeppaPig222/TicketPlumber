"""
Code Agent 插件
从 agents.code_agent 导入真实实现，作为 LazyAgentRegistry 可发现的插件暴露。
"""
import os
import sys

# 确保动态加载时能找到项目根目录下的 agents 包
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from agents.code_agent import CodeAgent as BaseCodeAgent


class CodeAgent(BaseCodeAgent):
    """代码与接口链路诊断 Agent（插件化入口）。"""
    pass
