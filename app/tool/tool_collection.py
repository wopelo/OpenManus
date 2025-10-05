"""Collection classes for managing multiple tools."""
from typing import Any, Dict, List

from app.exceptions import ToolError
from app.logger import logger
from app.tool.base import BaseTool, ToolFailure, ToolResult


class ToolCollection:
    """A collection of defined tools."""

    class Config:
        arbitrary_types_allowed = True

    # *tools 表示该方法可以接受任意数量的位置参数，所有传入的参数会被收集到一个元组中
    def __init__(self, *tools: BaseTool):
        self.tools = tools
        self.tool_map = {tool.name: tool for tool in tools}

    # __iter__ 方法使得 ToolCollection 类的实例可以被迭代
    def __iter__(self):
        # iter 是 Python 的内置函数，用于获取一个对象的迭代器
        return iter(self.tools)

    def to_params(self) -> List[Dict[str, Any]]:
        return [tool.to_param() for tool in self.tools]

    async def execute(
        # * 后面的参数必须以关键字形式传递
        self, *, name: str, tool_input: Dict[str, Any] = None
    ) -> ToolResult:
        """执行指定工具"""
        tool = self.tool_map.get(name)
        if not tool:
            return ToolFailure(error=f"Tool {name} is invalid")
        try:
            # 使用 ** 解包字典，将键值对作为关键字参数传递给工具
            result = await tool(**tool_input)
            return result
        except ToolError as e:
            return ToolFailure(error=e.message)

    async def execute_all(self) -> List[ToolResult]:
        """按顺序执行集合中的所有工具"""
        results = []
        for tool in self.tools:
            try:
                # 注意没有传入参数
                result = await tool()
                results.append(result)
            except ToolError as e:
                results.append(ToolFailure(error=e.message))
        return results

    def get_tool(self, name: str) -> BaseTool:
        return self.tool_map.get(name)

    def add_tool(self, tool: BaseTool):
        """向集合中添加单个工具。如果已存在同名工具，则将跳过该工具并记录警告。"""
        if tool.name in self.tool_map:
            logger.warning(f"Tool {tool.name} already exists in collection, skipping")
            return self

        # 元组创建之后不能修改，这里是重新绑定
        # 等同于 self.tools = self.tools + (tool,)
        self.tools += (tool,)
        self.tool_map[tool.name] = tool
        return self

    def add_tools(self, *tools: BaseTool):
        """Add multiple tools to the collection.

        If any tool has a name conflict with an existing tool, it will be skipped and a warning will be logged.
        """
        for tool in tools:
            self.add_tool(tool)
        return self
