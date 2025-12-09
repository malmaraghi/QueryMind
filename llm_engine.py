import ollama
import re


def is_dangerous_query(question):
    """Check if the user's question contains dangerous SQL keywords."""
    dangerous_keywords = [
        # Data modification
        r'\bdrop\b', r'\bdelete\b', r'\btruncate\b', r'\binsert\b',
        r'\bupdate\b', r'\breplace\b', r'\bmerge\b',
        # Schema modification
        r'\balter\b', r'\bcreate\b', r'\brename\b', r'\bmodify\b',
        # Permissions
        r'\bgrant\b', r'\brevoke\b',
        # Execution
        r'\bexec\b', r'\bexecute\b', r'\bcall\b',
        # Database/table operations
        r'\bload\b', r'\bimport\b', r'\bexport\b',
        r'\block\b', r'\bunlock\b',
        r'\bkill\b', r'\bshutdown\b', r'\breset\b',
        # File operations
        r'\boutfile\b', r'\binfile\b', r'\bdumpfile\b',
        # Dangerous commands
        r'\bset\b', r'\bflush\b', r'\bpurge\b',
    ]
    question_lower = question.lower()
    for pattern in dangerous_keywords:
        if re.search(pattern, question_lower):
            return True
    return False


def ask_llm(question, rag_context, model_name):
    # Block dangerous queries BEFORE sending to LLM
    if is_dangerous_query(question):
        return "Error: Only SELECT queries are allowed."
    
    # Check if schema context is valid before proceeding
    if not rag_context or rag_context.startswith("ERROR:") or rag_context.startswith("Error"):
        return f"Error: {rag_context}"
    
    prompt = f"""You are an expert SQL assistant for MariaDB.
Generate a valid SQL SELECT query based on the user's question and the provided database schema.

RULES:
1. Use ONLY the exact table names and column names shown in the schema
2. Output ONLY the raw SQL SELECT query - no explanations, no markdown, no code blocks
3. Use proper SQL syntax with correct quotes and operators

Database Schema:
{rag_context}

User Question: {question}

SQL Query:"""

    try:
        response = ollama.chat(
            model=model_name,
            messages=[{"role": "user", "content": prompt}]
        )
        sql_query = response["message"]["content"]
        sql_query = sql_query.strip().replace("```sql", "").replace("```", "").strip()
        
        if sql_query.lower().startswith("sql query:"):
            sql_query = sql_query[10:].strip()
        
        return sql_query
    except Exception as e:
        return f"LLM Error: {e}"