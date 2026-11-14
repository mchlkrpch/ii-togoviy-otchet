import ast
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


CENT = Decimal("0.01")
MONEY_PATTERN = re.compile(r"(?<![\d/.])[-−]?\d(?:[\d \u00a0\u202f]*\d)?[,.]\d{2}(?![\d./])")


def decimal_value(value):
    if value is None or isinstance(value, bool):
        raise ValueError("Число отсутствует")
    text = str(value).strip().replace("−", "-")
    text = re.sub(r"\s+", "", text).replace(",", ".")
    try:
        result = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(f"Некорректное число: {value}") from exc
    if not result.is_finite() or abs(result) > Decimal("1e18"):
        raise ValueError("Число должно быть конечным и не превышать 10¹⁸")
    return result


def money(value):
    return format(decimal_value(value).quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def find_money(text):
    return [(match.group(), money(match.group())) for match in MONEY_PATTERN.finditer(text)]


def formula_references(expression):
    if not isinstance(expression, str) or len(expression) > 4000:
        raise ValueError("Некорректная длина формулы")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Проверьте синтаксис формулы") from exc
    nodes = list(ast.walk(tree))
    if len(nodes) > 500:
        raise ValueError("В формуле слишком много операций")
    allowed = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load, ast.Constant,
               ast.Add, ast.Sub, ast.Mult, ast.Div, ast.UAdd, ast.USub)
    function_nodes = set()
    for node in nodes:
        if not isinstance(node, allowed):
            raise ValueError("Недопустимая операция в формуле")
        if isinstance(node, ast.Constant) and type(node.value) not in (int, float):
            raise ValueError("В формуле допускаются только числовые константы")
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id.upper() not in {"SUM", "MIN", "MAX", "AVERAGE", "ROUND"} or node.keywords:
                raise ValueError("Недопустимая функция в формуле")
            function_nodes.add(id(node.func))
    return {node.id for node in nodes if isinstance(node, ast.Name) and id(node) not in function_nodes}


def evaluate_formula(expression, values):
    """Evaluate arithmetic over named inputs. Never execute Python or Excel code."""
    formula_references(expression)
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Проверьте синтаксис формулы") from exc
    if len(list(ast.walk(tree))) > 500:
        raise ValueError("В формуле слишком много операций")
    references = set()

    def visit(node):
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return decimal_value(ast.get_source_segment(expression, node))
        if isinstance(node, ast.Name):
            references.add(node.id)
            if node.id not in values or values[node.id] is None:
                raise ValueError(f"Нет значения для {node.id}")
            return decimal_value(values[node.id])
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left), visit(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ValueError("Деление на ноль")
                return left / right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            arguments = [visit(argument) for argument in node.args]
            name = node.func.id.upper()
            if name == "SUM":
                return sum(arguments, Decimal(0))
            if name in ("MIN", "MAX", "AVERAGE") and arguments:
                if name == "MIN":
                    return min(arguments)
                if name == "MAX":
                    return max(arguments)
                return sum(arguments, Decimal(0)) / len(arguments)
            if name == "ROUND" and len(arguments) == 2:
                digits = arguments[1]
                if digits != digits.to_integral_value() or abs(digits) > 12:
                    raise ValueError("ROUND требует целое число знаков от -12 до 12")
                return arguments[0].quantize(Decimal(1).scaleb(-int(digits)), rounding=ROUND_HALF_UP)
        raise ValueError("Поддерживаются числа, ссылки, +, -, *, /, SUM, MIN, MAX, AVERAGE и ROUND")

    try:
        result = visit(tree)
    except (InvalidOperation, OverflowError, RecursionError) as exc:
        raise ValueError("Не удалось вычислить формулу") from exc
    return result, sorted(references)
