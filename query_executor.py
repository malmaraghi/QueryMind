import re


def extract_sql(text):
    """Extract SQL SELECT query from LLM output."""
    # Check if LLM returned an error message
    if text.startswith("Error:") or text.startswith("LLM Error:"):
        return text
    
    # Check for apology/refusal messages
    text_lower = text.lower().strip()
    if any(text_lower.startswith(phrase) for phrase in ["sorry", "i cannot", "i can't", "i'm sorry"]):
        return f"Error: {text.strip()}"
    
    # Remove <think>...</think> blocks (Qwen models)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    
    # Remove markdown code blocks
    text = re.sub(r'```sql\s*', '', text)
    text = re.sub(r'```\s*', '', text)
    
    # Try to find SELECT statement using regex
    select_match = re.search(r'(SELECT\s+.+?;)', text, re.IGNORECASE | re.DOTALL)
    if select_match:
        extracted = select_match.group(1).strip()
    else:
        # Try without requiring semicolon
        select_match = re.search(r'(SELECT\s+.+?)(?:\n\n|$)', text, re.IGNORECASE | re.DOTALL)
        if select_match:
            extracted = select_match.group(1).strip()
        else:
            # Fallback: try line-by-line extraction
            sql_lines = []
            in_sql_block = False
            for line in text.splitlines():
                line_stripped = line.strip()
                if re.match(r'^SELECT', line_stripped, re.IGNORECASE):
                    in_sql_block = True
                if in_sql_block:
                    sql_lines.append(line_stripped)
                    if line_stripped.endswith(';'):
                        break
            extracted = "\n".join(sql_lines) if sql_lines else text.strip()
    
    # Clean up extra whitespace
    extracted = extracted.strip()
    
    # Remove trailing content after semicolon
    if ';' in extracted:
        extracted = extracted[:extracted.index(';') + 1]
    
    # Check for dangerous SQL operations
    dangerous_patterns = r'\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|GRANT|REVOKE)\b'
    if re.search(dangerous_patterns, extracted, re.IGNORECASE):
        return "Error: Only SELECT queries are allowed. Detected unsafe operation."
    
    # Final validation
    if not re.match(r'^\s*SELECT', extracted, re.IGNORECASE):
        return "Error: Only SELECT queries are allowed."
    
    return extracted


def run_query(sql_query, connection):
    cursor = connection.cursor()
    try:
        cursor.execute(sql_query)
        rows = cursor.fetchall()
        
        if not rows:
            return "No records."
        
        column_names = [desc[0] for desc in cursor.description]
        return {"columns": column_names, "rows": rows}
    except Exception as error:
        return f"Error: {error}"
    finally:
        cursor.close()