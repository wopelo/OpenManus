"""File and directory manipulation tool with sandbox support.支持沙盒的文件和目录操作工具。"""

from collections import defaultdict
from pathlib import Path
from typing import Any, DefaultDict, List, Literal, Optional, get_args

from app.config import config
from app.exceptions import ToolError
from app.tool import BaseTool
from app.tool.base import CLIResult, ToolResult
from app.tool.file_operators import (FileOperator, LocalFileOperator, PathLike,
                                     SandboxFileOperator)

# Literal 用于指定变量或参数只能是特定的字面值
Command = Literal[
    "view",
    "create",
    "str_replace",
    "insert",
    "undo_edit",
]

# Constants
SNIPPET_LINES: int = 4
MAX_RESPONSE_LEN: int = 16000
TRUNCATED_MESSAGE: str = (
    "<response clipped><NOTE>To save on context only part of this file has been shown to you. "
    # grep 用于在文件中搜索指定的文本
    "You should retry this tool after you have searched inside the file with `grep -n` "
    "in order to find the line numbers of what you are looking for.</NOTE>"
)

# Tool description
_STR_REPLACE_EDITOR_DESCRIPTION = """Custom editing tool for viewing, creating and editing files
* State is persistent across command calls and discussions with the user
* If `path` is a file, `view` displays the result of applying `cat -n`. If `path` is a directory, `view` lists non-hidden files and directories up to 2 levels deep
* The `create` command cannot be used if the specified `path` already exists as a file
* If a `command` generates a long output, it will be truncated and marked with `<response clipped>`
* The `undo_edit` command will revert the last edit made to the file at `path`

Notes for using the `str_replace` command:
* The `old_str` parameter should match EXACTLY one or more consecutive lines from the original file. Be mindful of whitespaces!
* If the `old_str` parameter is not unique in the file, the replacement will not be performed. Make sure to include enough context in `old_str` to make it unique
* The `new_str` parameter should contain the edited lines that should replace the `old_str`
"""
# cat 命令用于连接和显示文件内容，-n 选项会在输出的每一行前面加上行号
# 上述文字的大意是：
# 用于查看、创建和编辑文件的自定义编辑工具
# *如果“path”是一个文件，则“view”显示应用“cat-n”的结果。如果`path`是一个目录，`view`将列出非隐藏文件和目录，深度可达2级
# *如果指定的“path”已作为文件存在，则无法使用“create”命令
# *如果一个“命令”生成一个长输出，它将被截断并标记为“<response clipped>”
# *“undo_edit”命令将恢复对“path”处的文件进行的最后一次编辑`

# 使用`str_replace`命令的注意事项：
# *`old_str`参数应与原始文件中的一行或多行连续行完全匹配。注意留白！
# *如果`old_str`参数在文件中不是唯一的，则不会执行替换。确保在`old_str`中包含足够的上下文，使其唯一
# *“new_str”参数应包含应替换“old_str”的已编辑行`

def maybe_truncate(
    content: str, truncate_after: Optional[int] = MAX_RESPONSE_LEN
) -> str:
    """Truncate content and append a notice if content exceeds the specified length."""
    # 如果内容超过指定长度，则截断内容并附加通知。
    if not truncate_after or len(content) <= truncate_after:
        return content
    return content[:truncate_after] + TRUNCATED_MESSAGE


class StrReplaceEditor(BaseTool):
    """A tool for viewing, creating, and editing files with sandbox support.支持沙盒的文件和目录操作工具"""

    name: str = "str_replace_editor"
    description: str = _STR_REPLACE_EDITOR_DESCRIPTION
    parameters: dict = {
        "type": "object",
        "properties": {
            "command": {
                "description": "The commands to run. Allowed options are: `view`, `create`, `str_replace`, `insert`, `undo_edit`.",
                "enum": ["view", "create", "str_replace", "insert", "undo_edit"],
                "type": "string",
            },
            "path": {
                "description": "Absolute path to file or directory.",
                "type": "string",
            },
            "file_text": {
                "description": "Required parameter of `create` command, with the content of the file to be created.",
                "type": "string",
            },
            "old_str": {
                "description": "Required parameter of `str_replace` command containing the string in `path` to replace.",
                "type": "string",
            },
            "new_str": {
                "description": "Optional parameter of `str_replace` command containing the new string (if not given, no string will be added). Required parameter of `insert` command containing the string to insert.",
                "type": "string",
            },
            # 插入行，`new_str`将插入到 insert_line 之后。
            "insert_line": {
                "description": "Required parameter of `insert` command. The `new_str` will be inserted AFTER the line `insert_line` of `path`.",
                "type": "integer",
            },
            # 查看范围，例如[11,12]将显示第11行和第12行
            "view_range": {
                "description": "Optional parameter of `view` command when `path` points to a file. If none is given, the full file is shown. If provided, the file will be shown in the indicated line number range, e.g. [11, 12] will show lines 11 and 12. Indexing at 1 to start. Setting `[start_line, -1]` shows all lines from `start_line` to the end of the file.",
                "items": {"type": "integer"},
                "type": "array",
            },
        },
        "required": ["command", "path"],
    }
    _file_history: DefaultDict[PathLike, List[str]] = defaultdict(list)
    # 本地文件操作对象，提供 读、写、判断路径是否是文件夹、判断路径是否存在、运行命令 等基础方法
    _local_operator: LocalFileOperator = LocalFileOperator()
    # 沙盒文件操作对象，提供 读、写、判断路径是否是文件夹、判断路径是否存在、运行命令 等基础方法
    _sandbox_operator: SandboxFileOperator = SandboxFileOperator()

    # def _get_operator(self, use_sandbox: bool) -> FileOperator:
    def _get_operator(self) -> FileOperator:
        """Get the appropriate file operator based on execution mode.根据配置获取文件操作对象"""
        return (
            self._sandbox_operator
            if config.sandbox.use_sandbox
            else self._local_operator
        )

    async def execute(
        self,
        *,
        command: Command,
        path: str,
        file_text: str | None = None,
        view_range: list[int] | None = None,
        old_str: str | None = None,
        new_str: str | None = None,
        insert_line: int | None = None,
        **kwargs: Any,
    ) -> str:
        """Execute a file operation command."""
        # 获取文件操作对象
        operator = self._get_operator()

        # 根据执行环境验证路径和命令组合是否合法
        await self.validate_path(command, Path(path), operator)

        # Execute the appropriate command
        if command == "view":
            result = await self.view(path, view_range, operator)
        elif command == "create":
            if file_text is None:
                raise ToolError("Parameter `file_text` is required for command: create")
            await operator.write_file(path, file_text)
            self._file_history[path].append(file_text)
            result = ToolResult(output=f"File created successfully at: {path}")
        elif command == "str_replace":
            if old_str is None:
                raise ToolError(
                    "Parameter `old_str` is required for command: str_replace"
                )
            result = await self.str_replace(path, old_str, new_str, operator)
        elif command == "insert":
            if insert_line is None:
                raise ToolError(
                    "Parameter `insert_line` is required for command: insert"
                )
            if new_str is None:
                raise ToolError("Parameter `new_str` is required for command: insert")
            result = await self.insert(path, insert_line, new_str, operator)
        elif command == "undo_edit":
            result = await self.undo_edit(path, operator)
        else:
            # This should be caught by type checking, but we include it for safety
            raise ToolError(
                f'Unrecognized command {command}. The allowed commands for the {self.name} tool are: {", ".join(get_args(Command))}'
            )

        return str(result)

    async def validate_path(
        self, command: str, path: Path, operator: FileOperator
    ) -> None:
        """Validate path and command combination based on execution environment.根据执行环境验证路径和命令组合"""
        # Check if path is absolute
        if not path.is_absolute():
            raise ToolError(f"The path {path} is not an absolute path")

        # Only check if path exists for non-create commands
        if command != "create":
            if not await operator.exists(path):
                raise ToolError(
                    f"The path {path} does not exist. Please provide a valid path."
                )

            # Check if path is a directory
            is_dir = await operator.is_directory(path)
            if is_dir and command != "view":
                raise ToolError(
                    f"The path {path} is a directory and only the `view` command can be used on directories"
                )

        # Check if file exists for create command
        elif command == "create":
            exists = await operator.exists(path)
            if exists:
                raise ToolError(
                    f"File already exists at: {path}. Cannot overwrite files using command `create`."
                )

    async def view(
        self,
        path: PathLike,
        view_range: Optional[List[int]] = None,
        operator: FileOperator = None,
    ) -> CLIResult:
        """Display file or directory content."""
        # Determine if path is a directory
        is_dir = await operator.is_directory(path)

        if is_dir:
            # Directory handling
            if view_range:
                raise ToolError(
                    "The `view_range` parameter is not allowed when `path` points to a directory."
                )

            return await self._view_directory(path, operator)
        else:
            # File handling
            return await self._view_file(path, operator, view_range)

    @staticmethod
    async def _view_directory(path: PathLike, operator: FileOperator) -> CLIResult:
        """Display directory contents."""
        # 构造find命令，查找目录下最多2层深度的文件和文件夹，排除隐藏项目
        find_cmd = f"find {path} -maxdepth 2 -not -path '*/\\.*'"

        # Execute command using the operator
        returncode, stdout, stderr = await operator.run_command(find_cmd)

        if not stderr:
            stdout = (
                f"Here's the files and directories up to 2 levels deep in {path}, "
                f"excluding hidden items:\n{stdout}\n"
            )

        return CLIResult(output=stdout, error=stderr)

    async def _view_file(
        self,
        path: PathLike,
        operator: FileOperator,
        view_range: Optional[List[int]] = None,
    ) -> CLIResult:
        """Display file content, optionally within a specified line range."""
        # 先读取全部内容
        file_content = await operator.read_file(path)
        init_line = 1

        # Apply view range if specified
        if view_range:
            if len(view_range) != 2 or not all(isinstance(i, int) for i in view_range):
                raise ToolError(
                    "Invalid `view_range`. It should be a list of two integers."
                )

            file_lines = file_content.split("\n")
            n_lines_file = len(file_lines)
            init_line, final_line = view_range

            # 判断行范围是否合法
            if init_line < 1 or init_line > n_lines_file:
                raise ToolError(
                    f"Invalid `view_range`: {view_range}. Its first element `{init_line}` should be "
                    f"within the range of lines of the file: {[1, n_lines_file]}"
                )
            if final_line > n_lines_file:
                raise ToolError(
                    f"Invalid `view_range`: {view_range}. Its second element `{final_line}` should be "
                    f"smaller than the number of lines in the file: `{n_lines_file}`"
                )
            if final_line != -1 and final_line < init_line:
                raise ToolError(
                    f"Invalid `view_range`: {view_range}. Its second element `{final_line}` should be "
                    f"larger or equal than its first `{init_line}`"
                )

            # 根据行范围裁剪内容
            if final_line == -1:
                file_content = "\n".join(file_lines[init_line - 1 :])
            else:
                file_content = "\n".join(file_lines[init_line - 1 : final_line])

        # Format and return result
        return CLIResult(
            output=self._make_output(file_content, str(path), init_line=init_line)
        )

    async def str_replace(
        self,
        path: PathLike,
        old_str: str,
        new_str: Optional[str] = None,
        operator: FileOperator = None,
    ) -> CLIResult:
        """Replace a unique string in a file with a new string."""
        # Read file content and expand tabs
        # expandtabs 用于将字符串中的制表符（tab）替换为空格
        file_content = (await operator.read_file(path)).expandtabs()
        old_str = old_str.expandtabs()
        new_str = new_str.expandtabs() if new_str is not None else ""

        # 检查旧字符串是否唯一出现
        occurrences = file_content.count(old_str)
        if occurrences == 0:
            raise ToolError(
                f"No replacement was performed, old_str `{old_str}` did not appear verbatim in {path}."
            )
        elif occurrences > 1:
            # Find line numbers of occurrences
            file_content_lines = file_content.split("\n")
            lines = [
                idx + 1
                for idx, line in enumerate(file_content_lines)
                if old_str in line
            ]
            raise ToolError(
                f"No replacement was performed. Multiple occurrences of old_str `{old_str}` "
                f"in lines {lines}. Please ensure it is unique"
            )

        # 通过字符串的 replace 方法进行替换
        new_file_content = file_content.replace(old_str, new_str)

        # 将新内容写入文件
        await operator.write_file(path, new_file_content)

        # 保存原内容到历史记录
        self._file_history[path].append(file_content)

        # Create a snippet of the edited section
        replacement_line = file_content.split(old_str)[0].count("\n")
        start_line = max(0, replacement_line - SNIPPET_LINES)
        end_line = replacement_line + SNIPPET_LINES + new_str.count("\n")
        snippet = "\n".join(new_file_content.split("\n")[start_line : end_line + 1])

        # Prepare the success message
        success_msg = f"The file {path} has been edited. "
        success_msg += self._make_output(
            snippet, f"a snippet of {path}", start_line + 1
        )
        success_msg += "Review the changes and make sure they are as expected. Edit the file again if necessary."

        return CLIResult(output=success_msg)

    async def insert(
        self,
        path: PathLike,
        insert_line: int,
        new_str: str,
        operator: FileOperator = None,
    ) -> CLIResult:
        """Insert text at a specific line in a file."""
        # Read and prepare content
        file_text = (await operator.read_file(path)).expandtabs()
        new_str = new_str.expandtabs()
        file_text_lines = file_text.split("\n")
        n_lines_file = len(file_text_lines)

        # 校验插入行号是否有效
        if insert_line < 0 or insert_line > n_lines_file:
            raise ToolError(
                f"Invalid `insert_line` parameter: {insert_line}. It should be within "
                f"the range of lines of the file: {[0, n_lines_file]}"
            )

        # 执行插入
        new_str_lines = new_str.split("\n")
        new_file_text_lines = (
            file_text_lines[:insert_line]
            + new_str_lines
            + file_text_lines[insert_line:]
        )

        # 创建预览片段
        snippet_lines = (
            file_text_lines[max(0, insert_line - SNIPPET_LINES) : insert_line]
            + new_str_lines
            + file_text_lines[insert_line : insert_line + SNIPPET_LINES]
        )

        # Join lines and write to file
        new_file_text = "\n".join(new_file_text_lines)
        snippet = "\n".join(snippet_lines)

        await operator.write_file(path, new_file_text)
        self._file_history[path].append(file_text)

        # Prepare success message
        success_msg = f"The file {path} has been edited. "
        success_msg += self._make_output(
            snippet,
            "a snippet of the edited file",
            max(1, insert_line - SNIPPET_LINES + 1),
        )
        success_msg += "Review the changes and make sure they are as expected (correct indentation, no duplicate lines, etc). Edit the file again if necessary."

        return CLIResult(output=success_msg)

    async def undo_edit(
        self, path: PathLike, operator: FileOperator = None
    ) -> CLIResult:
        """Revert the last edit made to a file."""
        if not self._file_history[path]:
            raise ToolError(f"No edit history found for {path}.")

        old_text = self._file_history[path].pop()
        await operator.write_file(path, old_text)

        return CLIResult(
            output=f"Last edit to {path} undone successfully. {self._make_output(old_text, str(path))}"
        )

    def _make_output(
        self,
        file_content: str,
        file_descriptor: str,
        init_line: int = 1,
        expand_tabs: bool = True,
    ) -> str:
        """Format file content for display with line numbers.用行号格式化文件内容以供显示"""
        file_content = maybe_truncate(file_content)
        if expand_tabs:
            file_content = file_content.expandtabs()

        # Add line numbers to each line
        file_content = "\n".join(
            [
                f"{i + init_line:6}\t{line}"
                for i, line in enumerate(file_content.split("\n"))
            ]
        )

        return (
            f"Here's the result of running `cat -n` on {file_descriptor}:\n"
            + file_content
            + "\n"
        )
