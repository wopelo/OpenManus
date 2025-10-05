from typing import Any, List, Optional, Type, Union, get_args, get_origin

from pydantic import BaseModel, Field

from app.tool import BaseTool


class CreateChatCompletion(BaseTool):
    """使用指定的格式输出结果"""
    name: str = "create_chat_completion"
    description: str = (
        "Creates a structured completion with specified output formatting."
    )

    # Type mapping for JSON schema
    type_mapping: dict = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
        dict: "object",
        list: "array",
    }
    # Type 用来表示类型，即 response_type 的作用不是存储值，而是存储“这个值应该是什么类型”。
    response_type: Optional[Type] = None
    required: List[str] = Field(default_factory=lambda: ["response"])

    def __init__(self, response_type: Optional[Type] = str):
        """Initialize with a specific response type."""
        super().__init__()
        self.response_type = response_type
        self.parameters = self._build_parameters()

    def _build_parameters(self) -> dict:
        """Build parameters schema based on response type."""
        # 若response_type为str，返回包含字符串类型响应的模式
        if self.response_type == str:
            return {
                "type": "object",
                "properties": {
                    "response": {
                        "type": "string",
                        "description": "The response text that should be delivered to the user.",
                    },
                },
                "required": self.required,
            }

        # 若response_type是继承自BaseModel的类，获取其JSON模式
        if isinstance(self.response_type, type) and issubclass(
            self.response_type, BaseModel
        ):
            schema = self.response_type.model_json_schema()
            return {
                "type": "object",
                "properties": schema["properties"],
                "required": schema.get("required", self.required),
            }

        # 调用_create_type_schema方法处理其他类型
        return self._create_type_schema(self.response_type)

    def _create_type_schema(self, type_hint: Type) -> dict:
        """Create a JSON schema for the given type."""
        # 获取一个泛型类型的基本类型
        # get_origin(List[int]) 返回 <class 'list'>
        # get_origin(str) 返回 None（非泛型类型）
        origin = get_origin(type_hint)
        # 获取泛型类型的参数
        # get_args(List[int]) 返回 (int,)
        # get_args(str) 返回 ()
        args = get_args(type_hint)

        # 处理基本类型
        if origin is None:
            return {
                "type": "object",
                "properties": {
                    "response": {
                        "type": self.type_mapping.get(type_hint, "string"),
                        "description": f"Response of type {type_hint.__name__}",
                    }
                },
                "required": self.required,
            }

        # 处理列表类型
        if origin is list:
            item_type = args[0] if args else Any
            return {
                "type": "object",
                "properties": {
                    "response": {
                        "type": "array",
                        "items": self._get_type_info(item_type), # 递归处理子元素类型
                    }
                },
                "required": self.required,
            }

        # 处理字典类型
        if origin is dict:
            value_type = args[1] if len(args) > 1 else Any
            return {
                "type": "object",
                "properties": {
                    "response": {
                        "type": "object",
                        "additionalProperties": self._get_type_info(value_type),
                    }
                },
                "required": self.required,
            }

        # 处理联合类型
        if origin is Union:
            return self._create_union_schema(args)

        # 这里是否应该返回一个默认的 schema 更好一些？目前的写法可能会导致无限递归循环
        return self._build_parameters()

    def _get_type_info(self, type_hint: Type) -> dict:
        """Get type information for a single type.获取单个类型的类型信息"""
        if isinstance(type_hint, type) and issubclass(type_hint, BaseModel):
            return type_hint.model_json_schema()

        return {
            "type": self.type_mapping.get(type_hint, "string"),
            "description": f"Value of type {getattr(type_hint, '__name__', 'any')}",
        }

    def _create_union_schema(self, types: tuple) -> dict:
        """Create schema for Union types.为Union类型创建schema"""
        return {
            "type": "object",
            "properties": {
                "response": {"anyOf": [self._get_type_info(t) for t in types]}
            },
            "required": self.required,
        }

    async def execute(self, required: list | None = None, **kwargs) -> Any:
        """Execute the chat completion with type conversion.

        Args:
            required: List of required field names or None
            **kwargs: Response data

        Returns:
            Converted response based on response_type
        """
        required = required or self.required

        # Handle case when required is a list
        if isinstance(required, list) and len(required) > 0:
            if len(required) == 1:
                required_field = required[0]
                result = kwargs.get(required_field, "")
            else:
                # Return multiple fields as a dictionary
                return {field: kwargs.get(field, "") for field in required}
        else:
            required_field = "response"
            result = kwargs.get(required_field, "")

        # Type conversion logic
        if self.response_type == str:
            return result

        if isinstance(self.response_type, type) and issubclass(
            self.response_type, BaseModel
        ):
            return self.response_type(**kwargs)

        if get_origin(self.response_type) in (list, dict):
            return result  # Assuming result is already in correct format

        try:
            return self.response_type(result)
        except (ValueError, TypeError):
            return result
