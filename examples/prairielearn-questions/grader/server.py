import ast
import base64

def generate(data):
    pass

def grade(data):
    # 1. Fetch student submission from the file editor
    submitted_files = data["submitted_answers"].get("_files", [])
    student_code = ""
    
    # iterate through file uploaded and find the one of our editor
    for f in submitted_files:
        if f.get("name") == "student_code.py":
            student_code = base64.b64decode(f.get("contents")).decode("utf-8")
            break
            
    if not student_code.strip():
        data["format_errors"]["_files"] = "Error: Please enter your code."
        return

    # 2. Strict Formatting Rules (Preventing one-liners via semicolons)
    if ";" in student_code:
        data["format_errors"]["_files"] = "Syntax Rule: Semicolons are not allowed. Please use proper line breaks."
        return

    lines = [line.strip() for line in student_code.strip().split('\n') if line.strip()]
    if len(lines) > 3:
        data["format_errors"]["_files"] = "Optimization Rule: Your solution should not exceed 3 lines of code."
        return

    # 3. Security Hardening (AST Parsing)
    try:
        parsed_ast = ast.parse(student_code)
    except SyntaxError:
        data["format_errors"]["_files"] = "Syntax Error: Python could not parse your code."
        return

    for node in ast.walk(parsed_ast):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            data["format_errors"]["_files"] = "Security Exception: Import statements are restricted."
            return
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                allowed_functions = ['float', 'int', 'round']
                if node.func.id not in allowed_functions:
                    data["format_errors"]["_files"] = f"Security Exception: Function '{node.func.id}()' is not allowed here."
                    return

    # 4. Safe Execution Sandbox
    local_env = {
        'price': '15.50', 
        'tip_rate': '20'  
    }
    
    safe_globals = {"__builtins__": {"float": float, "int": int}}

    try:
        exec(student_code, safe_globals, local_env)
    except Exception as e:
        data["format_errors"]["_files"] = f"Runtime Error: {str(e)}"
        return

    # 5. Final State Assertion
    score = 0.0
    if type(local_env.get('price')) is float:
        score += 0.5
    if type(local_env.get('tip_rate')) is int or type(local_env.get('tip_rate')) is float:
        score += 0.5

    data["score"] = score