import ollama
import re


def is_dangerous_query(question):
    """Check if the user's question contains dangerous SQL keywords."""
    dangerous_keywords = [
        r'\bdrop\b', r'\bdelete\b', r'\btruncate\b', r'\binsert\b',
        r'\bupdate\b', r'\breplace\b', r'\bmerge\b',
        r'\balter\b', r'\bcreate\b', r'\brename\b', r'\bmodify\b',
        r'\bgrant\b', r'\brevoke\b',
        r'\bexec\b', r'\bexecute\b', r'\bcall\b',
        r'\bload\b', r'\bimport\b', r'\bexport\b',
        r'\block\b', r'\bunlock\b',
        r'\bkill\b', r'\bshutdown\b', r'\breset\b',
        r'\boutfile\b', r'\binfile\b', r'\bdumpfile\b',
        r'\bset\b', r'\bflush\b', r'\bpurge\b',
    ]
    question_lower = question.lower()
    for pattern in dangerous_keywords:
        if re.search(pattern, question_lower):
            return True
    return False


def ask_llm(question, rag_context, model_name, options=None, client=None):
    """``options`` is passed to Ollama as-is (e.g. temperature, seed); the app leaves it unset.

    ``client`` is an optional ``ollama.Client`` (used by the experiment runner to enforce a
    request timeout); the app uses the default module-level client.
    """
    if is_dangerous_query(question):
        return "Error: Only SELECT queries are allowed."
    
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
        chat_kwargs = {"options": options} if options else {}
        chat = client.chat if client is not None else ollama.chat
        response = chat(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            **chat_kwargs
        )
        sql_query = response["message"]["content"]
        sql_query = sql_query.strip().replace("```sql", "").replace("```", "").strip()
        
        if sql_query.lower().startswith("sql query:"):
            sql_query = sql_query[10:].strip()
        
        return sql_query
    except Exception as e:
        return f"LLM Error: {e}"