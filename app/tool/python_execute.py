import multiprocessing
import sys
from io import StringIO
from typing import Dict

from app.tool.base import BaseTool


class PythonExecute(BaseTool):
    """A tool for executing Python code with timeout and safety restrictions."""

    name: str = "python_execute"
    description: str = "Executes Python code string. Note: Only print outputs are visible, function return values are not captured. Use print statements to see results."
    """执行Python代码字符串。注意：只有打印输出可见，函数返回值不会被捕获。使用打印语句查看结果。"""
    parameters: dict = {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": "The Python code to execute.",
            },
        },
        "required": ["code"],
    }

    def _run_code(self, code: str, result_dict: dict, safe_globals: dict) -> None:
        """安全执行Python代码并捕获输出"""

        # 保存当前的标准输出流，以便后续恢复
        original_stdout = sys.stdout
        try:
            # 创建一个内存中的字符串缓冲区，用于捕获代码执行期间的输出
            output_buffer = StringIO()
            # 将标准输出重定向到缓冲区，这样代码中所有的 print 输出都会被捕捉到 output_buffer 中
            sys.stdout = output_buffer
            # 在指定的安全全局命名空间中执行传入的 Python 代码字符串
            exec(code, safe_globals, safe_globals)
            # 获取缓冲区中捕获的所有输出内容，并存入结果字典
            result_dict["observation"] = output_buffer.getvalue()
            # 标记代码执行成功
            result_dict["success"] = True
        except Exception as e:
            # 如果执行过程中发生异常，则将异常信息存入结果字典
            result_dict["observation"] = str(e)
            # 标记代码执行失败
            result_dict["success"] = False
        finally:
            # 无论执行成功还是失败，都恢复原始的标准输出流
            sys.stdout = original_stdout

    async def execute(
        self,
        code: str,
        timeout: int = 5,
    ) -> Dict:
        """
        Executes the provided Python code with a timeout.

        Args:
            code (str): The Python code to execute.
            timeout (int): Execution timeout in seconds.

        Returns:
            Dict: Contains 'output' with execution output or error message and 'success' status.
        """

        # Manager 用于进程间通信
        with multiprocessing.Manager() as manager:
            result = manager.dict({"observation": "", "success": False})
            # __builtins__ 是 Python 中一个特殊的内置模块/命名空间，包含了Python解释器内置的所有函数、异常和常量
            if isinstance(__builtins__, dict):
                safe_globals = {"__builtins__": __builtins__}
            else:
                safe_globals = {"__builtins__": __builtins__.__dict__.copy()}
            # 启动子进程执行代码
            proc = multiprocessing.Process(
                target=self._run_code, args=(code, result, safe_globals)
            )
            proc.start()
            proc.join(timeout)

            # timeout process
            if proc.is_alive():
                proc.terminate()
                proc.join(1)
                return {
                    "observation": f"Execution timeout after {timeout} seconds",
                    "success": False,
                }
            return dict(result)
