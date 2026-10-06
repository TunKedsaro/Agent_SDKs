"""Original benchmark business tools plus JSON IPC and a real MCP fixture.

No LLM calls, agent loop, canned plan, or expected answers live here.
"""
import inspect
import json
import sys
from copy import deepcopy
from typing import Literal, get_args, get_origin

PLANNING_DATA = {}
BACKUP_SOURCES = {}
COMPANY = {}
STATEMENT = {}
error_tool_log = []
retry_limit_log = []
mcp_server_log = []


def calculator(
    operation: Literal["add", "subtract", "multiply", "divide"],
    a: float,
    b: float,
) -> float:
    """Calculate a + b, a - b, a * b, or a / b.

    Choose the operation and provide the two operands.
    For subtraction and division, operand order matters.
    """
    if operation == "add":
        return a + b
    if operation == "subtract":
        return a - b
    if operation == "multiply":
        return a * b
    if operation == "divide":
        if b == 0:
            raise ValueError("Cannot divide by zero.")
        return a / b

    raise ValueError(f"Unsupported operation: {operation}")

def search_company(query: str) -> dict:
    """Search company names and return matching company IDs and names."""
    normalized = query.strip().casefold()

    matches = (
        [COMPANY.copy()]
        if normalized and normalized in COMPANY["company_name"].casefold()
        else []
    )

    output = {"companies": matches}

    mcp_server_log.append({
        "tool": "search_company",
        "arguments": {"query": query},
        "output": output,
    })

    return output

def get_financial_statement(company_id: str, year: int) -> dict:
    """Retrieve a financial statement using a company ID and year."""
    found = (
        company_id == STATEMENT["company_id"]
        and year == STATEMENT["year"]
    )

    output = (
        STATEMENT.copy()
        if found
        else {"error": "STATEMENT_NOT_FOUND"}
    )

    mcp_server_log.append({
        "tool": "get_financial_statement",
        "arguments": {"company_id": company_id, "year": year},
        "output": output,
    })

    return output

def build_retry_mcp():
    server = FastMCP("Finance Retry Benchmark")
    trace = []
    state = {"statement_attempts": 0}

    @server.tool()
    def search_company(query: str) -> dict:
        """Search company names and return matching company IDs and names."""
        normalized = query.strip().casefold()

        output = {
            "companies": (
                [COMPANY.copy()]
                if normalized
                and normalized in COMPANY["company_name"].casefold()
                else []
            )
        }

        trace.append({
            "tool": "search_company",
            "arguments": {"query": query},
            "output": output,
        })

        return output

    @server.tool()
    def get_financial_statement(company_id: str, year: int) -> dict:
        """Retrieve a financial statement by company ID and year.

        On failure, the response includes an error code and retryable flag.
        """
        valid_request = (
            company_id == STATEMENT["company_id"]
            and year == STATEMENT["year"]
        )

        if not valid_request:
            output = {
                "error": {
                    "code": "STATEMENT_NOT_FOUND",
                    "retryable": False,
                }
            }
        else:
            state["statement_attempts"] += 1

            if state["statement_attempts"] == 1:
                output = {
                    "error": {
                        "code": "TEMPORARY_UNAVAILABLE",
                        "retryable": True,
                    }
                }
            else:
                output = STATEMENT.copy()

        trace.append({
            "tool": "get_financial_statement",
            "arguments": {"company_id": company_id, "year": year},
            "output": output,
        })

        return output

    return server, trace

def list_companies() -> list[dict]:
    """List available companies with their IDs and names."""
    return [
        {
            "company_id": row["company_id"],
            "company_name": row["company_name"],
        }
        for row in PLANNING_DATA.values()
    ]

def read_company_financials(company_id: str) -> dict:
    """Read a company's annual financial data.

    A null value means the data is unavailable.
    """
    row = PLANNING_DATA.get(company_id)

    if row is None:
        return {"error": "COMPANY_NOT_FOUND"}

    return deepcopy(row)

def calculate_percentage(
    numerator: float,
    denominator: float,
    mode: Literal["ratio", "growth"],
) -> dict:
    """Calculate a percentage.

    ratio: numerator / denominator * 100.
    growth: (numerator - denominator) / denominator * 100;
            numerator is the current value, denominator the previous value.
    """
    if denominator == 0:
        return {"value_pct": None, "error": "ZERO_DENOMINATOR"}

    if mode == "ratio":
        value = numerator / denominator * 100
    elif mode == "growth":
        value = (numerator - denominator) / denominator * 100
    else:
        raise ValueError("Unsupported mode")

    return {"value_pct": round(value, 2)}

def read_primary_financials(company_id: str) -> dict:
    """Read annual financial data from the primary source."""
    if company_id == "co-e42":
        return {
            "company_id": company_id,
            "error": "PRIMARY_SOURCE_UNAVAILABLE",
            "retryable": False,
        }

    row = PLANNING_DATA.get(company_id)

    if row is None:
        return {"error": "COMPANY_NOT_FOUND"}

    return deepcopy(row)

def find_financial_sources(company_id: str) -> list[dict]:
    """Find alternative financial documents for a company.

    Check each document's reporting year before using it.
    """
    return [
        {
            "source_id": source_id,
            "company_id": row["company_id"],
            "reporting_year": row["year"],
            "document_type": "annual_financial_statement",
        }
        for source_id, row in BACKUP_SOURCES.items()
        if row["company_id"] == company_id
    ]

def read_financial_source(source_id: str) -> dict:
    """Read a financial document by its source ID."""
    row = BACKUP_SOURCES.get(source_id)

    if row is None:
        return {"error": "SOURCE_NOT_FOUND"}

    return deepcopy(row)

def fetch_restricted_statement(company_name: str, year: int) -> dict:
    """Request a company's annual financial statement."""
    result = {
        "error": {
            "code": "ACCESS_DENIED",
            "retryable": False,
            "message": "The current account cannot access this statement.",
        }
    }
    error_tool_log.append({
        "tool": "fetch_restricted_statement",
        "arguments": {
            "company_name": company_name,
            "year": year,
        },
        "output": deepcopy(result),
    })
    return result

def fetch_temporarily_unavailable_statement(
    company_name: str,
    year: int,
) -> dict:
    """Request an annual financial statement from the data service."""
    result = {
        "error": {
            "code": "TEMPORARY_UNAVAILABLE",
            "retryable": True,
            "message": "The financial data service is temporarily unavailable.",
        }
    }

    retry_limit_log.append({
        "tool": "fetch_temporarily_unavailable_statement",
        "arguments": {
            "company_name": company_name,
            "year": year,
        },
        "output": deepcopy(result),
    })

    return result


TODOS = []
def write_todos(todos):
    """Replace the entire actionable task list with the supplied todos.

    Each item has content and status (pending, in_progress, completed).
    Reflect actual progress; update the list as work proceeds.
    """
    global TODOS
    TODOS = deepcopy(todos)
    return f"Updated todo list to {todos}"

BUSINESS = {fn.__name__: fn for fn in (
    calculator, list_companies, read_company_financials, calculate_percentage,
    read_primary_financials, find_financial_sources, read_financial_source,
    fetch_restricted_statement, fetch_temporarily_unavailable_statement, write_todos,
)}

def tool_specs(names):
    specs = []
    for name in names:
        fn = BUSINESS[name]
        if name == 'write_todos':
            properties = {'todos': {'type': 'array', 'items': {
                'type': 'object', 'properties': {
                    'content': {'type': 'string'},
                    'status': {'type': 'string', 'enum': ['pending', 'in_progress', 'completed']}
                }, 'required': ['content', 'status'], 'additionalProperties': False}}}
        else:
            properties = {}
            for key, parameter in inspect.signature(fn).parameters.items():
                annotation = parameter.annotation
                properties[key] = ({'type': 'string', 'enum': list(get_args(annotation))}
                    if get_origin(annotation) is Literal else
                    {'type': {str:'string', int:'integer', float:'number'}[annotation]})
        specs.append({'name': name, 'description': inspect.getdoc(fn), 'parameters': {
            'type':'object', 'properties':properties, 'required':list(properties),
            'additionalProperties':False}})
    return specs

def configure(fixtures):
    for name in ('PLANNING_DATA', 'BACKUP_SOURCES', 'COMPANY', 'STATEMENT'):
        globals()[name] = deepcopy(fixtures.get(name, {}))

def main():
    if len(sys.argv) > 1 and sys.argv[1] == '--mcp':
        global FastMCP
        from fastmcp import FastMCP
        with open(sys.argv[2], encoding='utf-8') as f:
            config = json.load(f)
        configure(config['fixtures'])
        if config.get('retry_fixture'):
            server, _ = build_retry_mcp()
        else:
            server = FastMCP('Finance Benchmark')
            server.tool()(search_company)
            server.tool()(get_financial_statement)
        server.run(transport='stdio', show_banner=False)
        return
    config = json.loads(sys.stdin.readline())
    configure(config.get('fixtures', {}))
    allowed = set(config['allowed_tools'])
    for line in sys.stdin:
        request = json.loads(line)
        try:
            name = request['name']
            if name not in allowed:
                raise ValueError('Tool is not allowed: ' + name)
            output = BUSINESS[name](**request['arguments'])
            reply = {'id':request['id'], 'output':output, 'error':None}
        except Exception as exc:
            reply = {'id':request['id'], 'output':None,
                     'error':{'type':type(exc).__name__, 'message':str(exc)}}
        print(json.dumps(reply, ensure_ascii=False, allow_nan=False), flush=True)

if __name__ == '__main__':
    main()

