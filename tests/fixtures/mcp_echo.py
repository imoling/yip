"""测试用的最小 stdio MCP 服务：echo 和 add。"""
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo")


@mcp.tool()
def echo(text: str) -> str:
    """原样返回"""
    return f"回声：{text}"


@mcp.tool()
def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b


if __name__ == "__main__":
    mcp.run()
