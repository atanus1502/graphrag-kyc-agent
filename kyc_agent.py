import os
from agents import Agent, Runner, function_tool
from agents.mcp import MCPServerStdio
from neo4j import GraphDatabase
from dotenv import load_dotenv
from schemas import CustomerAccountsInput, CustomerAccountsOutput, CustomerModel, AccountModel, TransactionModel, GenerateCypherRequest
import asyncio
from pydantic import BaseModel
from ollama import chat
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logging.getLogger("httpx").setLevel(logging.ERROR)
logger = logging.getLogger("KYC_AGENT")

# Load environment variables
load_dotenv()

# Read Neo4j environment variables into variables
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# Neo4j connection setup
def get_neo4j_driver():
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# Neo4j driver
driver = get_neo4j_driver()

# Tool 1: Get Customer details and its Accounts and some recent transactions
@function_tool
def get_customer_and_accounts(input: CustomerAccountsInput, tx_limit: int = 5) -> CustomerAccountsOutput:
    """
    Get Customer details including its Accounts and some recent transactions.
    Limits the number of most recent transactions per account.
    
    Args:
        input: CustomerAccountsInput containing customer_id
        tx_limit: Maximum number of recent transactions to return per account (default: 5)
    """
    logger.info(f"TOOL: GET_CUSTOMER_AND_ACCOUNTS - {input.customer_id}")

    with driver.session() as session:
        result = session.run(
            """
            MATCH (c:Customer {id: $customer_id})-[o:OWNS]->(a:Account)
            WITH c, a
            CALL (c,a) {
                MATCH (a)-[b:TO|FROM]->(t:Transaction)
                ORDER BY t.timestamp DESC
                LIMIT $tx_limit
                RETURN collect(t) as transactions
            }
            RETURN c as customer, a as account, transactions
            """,
            customer_id=input.customer_id,
            tx_limit=tx_limit
        )
        # Get the records from the result
        records = result.data()
        # Initialize lists to store the customer, accounts, and transactions
        accounts = []
        for record in records:
            customer = dict(record["customer"])
            account = dict(record["account"])
            account["transactions"] = [dict(t) for t in record["transactions"]]
            accounts.append(account)
            

        return CustomerAccountsOutput(
            customer=CustomerModel(**customer),
            accounts=[AccountModel(**a) for a in accounts]
        )
       

# Tool 2: Identify watchlisted customers in suspicious rings
@function_tool 
def find_customer_rings(max_number_rings: int = 10, customer_in_watchlist: bool = True, customer_is_pep: bool = False, customer_id: str = None):
    """
    Detects circular transaction patterns (up to 6 hops) involving high-risk customers.
    
    Finds account cycles where the accounts are owned by customers matching specified
    risk criteria (watchlisted and/or PEP status).
    
    Args:
        max_number_rings: Maximum rings to return (default: 10)
        customer_in_watchlist: Filter for watchlisted customers (default: True)
        customer_is_pep: Filter for PEP customers (default: False)
        customer_id: Specific customer to focus on (not implemented)
    
    Returns:
        dict: Contains ring paths and associated high-risk customers
    """
    logger.info(f"TOOL: FIND_CUSTOMER_RINGS - {max_number_rings} - {customer_in_watchlist} - {customer_is_pep}")
    with driver.session() as session:
        result = session.run(
            f"""
            MATCH p=(a:Account)-[:FROM|TO*6]->(a:Account)
            WITH p, [n IN nodes(p) WHERE n:Account] AS accounts
            UNWIND accounts AS acct
            MATCH (cust:Customer)-[r:OWNS]->(acct)
            WHERE cust.on_watchlist = $customer_in_watchlist AND cust.is_pep = $customer_is_pep
            WITH 
              p, 
              COLLECT(DISTINCT cust)   AS watchedCustomers,
              COLLECT(DISTINCT r)      AS watchRels
            RETURN 
              p, 
              watchedCustomers,
              watchRels
            LIMIT $max_number_rings
            """,
            max_number_rings=max_number_rings,
            customer_in_watchlist=customer_in_watchlist,
            customer_is_pep=customer_is_pep
        )
        rings = []
        for record in result:
            # Convert path to a list of node dictionaries for easier consumption
            path_nodes = [dict(node) for node in record["p"].nodes]
            watched_customers = [dict(cust) for cust in record["watchedCustomers"]]
            watch_rels = [dict(rel) for rel in record["watchRels"]]
            rings.append({
                "ring_path": path_nodes,
                "watched_customers": watched_customers,
                
            })
        
        return {"customer_rings": rings}

# Tool 3: Neo4j MCP server setup
neo4j_mcp_server = MCPServerStdio(
    params={
        "command": "uvx",
        "args": ["--with", "mcp<2", "mcp-neo4j-cypher@0.2.1"],
        "env": {
            "NEO4J_URI": NEO4J_URI,
            "NEO4J_USERNAME": NEO4J_USER,
            "NEO4J_PASSWORD": NEO4J_PASSWORD,
            "NEO4J_DATABASE": NEO4J_DATABASE,
        },
    },
    cache_tools_list=True,
    name="Neo4j MCP Server",
    client_session_timeout_seconds=20
)

# Tool 4: Create Memory node and link it to entities
@function_tool
def create_memory(content: str, customer_ids: list[str] = [], account_ids: list[str] = [], transaction_ids: list[str] = []) -> str:
    """
    Create a Memory node and link it to specified customers, accounts, and transactions
    """
    logger.info(f"TOOL: CREATE_MEMORY - {content} - {customer_ids} - {account_ids} - {transaction_ids}")
    with driver.session() as session:
        result = session.run(
            """
            CREATE (m:Memory {content: $content, created_at: datetime()})
            WITH m
            UNWIND $customer_ids as cid
            MATCH (c:Customer {id: cid})
            MERGE (m)-[:FOR_CUSTOMER]->(c)
            WITH m
            UNWIND $account_ids as aid
            MATCH (a:Account {id: aid})
            MERGE (m)-[:FOR_ACCOUNT]->(a)
            WITH m
            UNWIND $transaction_ids as tid
            MATCH (t:Transaction {id: tid})
            MERGE (m)-[:FOR_TRANSACTION]->(t)
            RETURN m.content as content
            """,
            content=content,
            customer_ids=customer_ids,
            account_ids=account_ids,
            transaction_ids=transaction_ids
        )
        
       
        return f"Created memory: {str(result)}"

# Tool 5: Text-to-Cypher Generation
@function_tool
def generate_cypher(request: GenerateCypherRequest) -> str:
    """
    Generate a Cypher query from natural language using a local finetuned text2cypher Ollama model
    """
    USER_INSTRUCTION = """Generate a Cypher query for the Question below:
    Use the information about the nodes, relationships, and properties from the Schema section below to generate the best possible Cypher query. 
    Return only the Cypher query as your final output, without any additional text or explanation.
    ####Schema:
    {schema}
    ####Question:
    {question}"""

    logger.info(f"TOOL: GENERATE_CYPHER - INPUT - {request.question}")
    user_message = USER_INSTRUCTION.format(
        schema=request.database_schema, 
        question=request.question
    )
    # Generate Cypher query using the text2cypher model
    model: str = "ed-neo4j/t2c-gemma3-4b-it-q8_0-35k"
    response = chat(
        model=model,
        messages=[{"role": "user", "content": user_message}]
    )
    generated_cypher = response['message']['content']
    # Replace \n with new line
    generated_cypher = generated_cypher.replace("\\n", "\n")

    print(f"GENERATED CYPHER: - OUTPUT - {generated_cypher}")
    
    return generated_cypher


async def main():
    await neo4j_mcp_server.connect()  # Connect the MCP server before using it

    # Define the instructions for the agent
    instructions = """You are a KYC analyst with access to a knowledge graph. Use the tools to answer questions about customers, accounts, and suspicious patterns.
    You are also a Neo4j expert and can use the Neo4j MCP server to query the graph.
    If you get a question about the KYC database that you can not answer with GraphRAG tools, you should
    - use the Neo4j MCP server to get the schema of the graph (if needed)
    - use the generate_cypher tool to generate a Cypher query from question and the schema
    - use the Neo4j MCP server to query the graph to answer the question
    """

    kyc_agent = Agent(
        name="KYC Analyst",
        instructions=instructions,
        tools=[get_customer_and_accounts, find_customer_rings, create_memory, generate_cypher],
        mcp_servers=[neo4j_mcp_server]
    )
    
    # Initialize conversation history
    conversation_history = []
    
    while True:
        query = input("Enter your KYC query (or 'quit' to exit): ")
        if query.lower() == 'quit':
            break
            
        # Run the agent with conversation history
        result = await Runner.run(
            kyc_agent, 
            conversation_history + [{"role": "user", "content": query}]
        )
        
        # Add the new interaction to conversation history
        conversation_history.extend([
            {"role": "user", "content": query},
            {"role": "assistant", "content": result.final_output}
        ])
        
        print(result.final_output)

    # Clean up
    await neo4j_mcp_server.cleanup()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        # Ensure we clean up any remaining resources
        driver.close() 
        