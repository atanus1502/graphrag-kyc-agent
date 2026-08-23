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


@function_tool
def get_customer_info(customer_id: str) -> dict:
    """
    Given a customer_id, return all information in the Customer node and the name of all Accounts owned by this customer.
    Args:
        customer_id (str): The ID of the customer to look up.
    Returns:
        dict: Customer information and a list of account names owned by this customer.
    """
    logger.info(f"TOOL: GET_CUSTOMER_INFO - {customer_id}")
    with driver.session() as session:
        result = session.run(
            """
            MATCH (c:Customer {id: $customer_id})-[OWNS]->(a:Account)
            RETURN c.id as customer_id, c.name as customer_name, c.on_watchlist as customer_on_watchlist, c.is_pep as customer_is_pep, a.name as account_name
            """,
            customer_id=customer_id
        )
        account_names = []
        customer_data = None
        for record in result:
            # Extract customer fields from the record
            customer_data = {
                "id": record["customer_id"],
                "name": record["customer_name"],
                "on_watchlist": record["customer_on_watchlist"],
                "is_pep": record["customer_is_pep"]
            }
            account_names.append(record["account_name"])
        if customer_data is not None:
            return {"customer": customer_data, "account_names": account_names}
        else:
            return {"customer": None, "account_names": []}

@function_tool
def find_customers_in_rings(limit: int = 50) -> list[dict]:
    """
    Find customers of interest (on watchlist or PEP) involved in account rings (cycles up to 6 hops).
    Args:
        is_customer_in_watchlist (bool): Filter for customers on watchlist.
        is_customer_pep (bool): Filter for customers who are PEPs.
        limit (int): Maximum number of results to return.
    Returns:
        list[dict]: List of dicts with customer and account info in the ring.
    """
    logger.info(f"TOOL: FIND_CUSTOMERS_IN_RINGS - limit={limit}")
    with driver.session() as session:
        result = session.run(
            """
            /* suspicious ring pattern - Money goes from an account through several accounts  transactions and returns to the original account */
            MATCH p=(a:Account)-[:FROM|TO*6]->(a:Account)
            /* identify customers involved in the rings who are either on watchlist or politically exposed.  */
            WITH p, [n IN nodes(p) WHERE n:Account] AS accounts
            UNWIND accounts AS acct
            MATCH (cust:Customer)-[r:OWNS]->(acct)
            WHERE cust.on_watchlist = TRUE OR cust.is_pep = TRUE
            WITH 
                cust,
                collect(DISTINCT acct.name) AS accounts_in_ring
            RETURN 
                cust.name   AS customer_name,
                cust.id     AS customer_id,
                cust.on_watchlist          AS customer_on_watchlist,
                cust.is_pep                AS customer_politically_exposed,
                accounts_in_ring           AS customer_accounts_in_ring
                ORDER BY customer_name ASC
            LIMIT $limit
            """,
            limit=limit
        )
        customers = []
        for record in result:
            customers.append({
                "customer_name": record["customer_name"],
                "customer_id": record["customer_id"],
                "customer_on_watchlist": record["customer_on_watchlist"],
                "customer_politically_exposed": record["customer_politically_exposed"],
                "customer_account_in_ring": record["customer_accounts_in_ring"]
            })
        return customers

@function_tool
def is_customer_in_suspicious_ring(customer_id: str) -> bool:
    """
    Returns True if the customer is involved in a suspicious ring (cycle up to 6 hops), otherwise False.
    Args:
        customer_id (str): The ID of the customer to check.
    Returns:
        bool: True if involved in a suspicious ring, False otherwise.
    """
    logger.info(f"TOOL: IS_CUSTOMER_IN_SUSPICIOUS_RING - {customer_id}")
    with driver.session() as session:
        result = session.run(
            """
            MATCH (c:Customer {id:$customer_id})
            WITH c, 
            EXISTS { 
              MATCH (c)-[:OWNS]->(:Account)-[:FROM|TO*6]->(:Account)
            } AS involved
            RETURN involved
            """,
            customer_id=customer_id
        )
        record = result.single()
        if record is not None:
            return bool(record["involved"])
        return False

@function_tool
def is_customer_bridge(customer_id: str) -> dict:
    """
    Returns customer details if the customer is employed by more than 2 companies, otherwise returns None.
    Args:
        customer_id (str): The ID of the customer to check.
    Returns:
        dict: Customer details with employer information if employed by more than 2 companies, None otherwise.
    """
    logger.info(f"TOOL: IS_CUSTOMER_BRIDGE - {customer_id}")
    with driver.session() as session:
        result = session.run(
            """
            MATCH (c:Customer {id: $customer_id})-[:EMPLOYED_BY]->(co:Company)
            WITH collect(co.name) AS employer_names, count(*) AS numEmployers, c
            WHERE numEmployers > 2 
            RETURN c.id, c.name, c.on_watchlist, c.is_pep, employer_names
            """,
            customer_id=customer_id
        )
        record = result.single()
        if record is not None:
            return {
                "customer_id": record["c.id"],
                "customer_name": record["c.name"], 
                "on_watchlist": record["c.on_watchlist"],
                "is_pep": record["c.is_pep"],
                "employer_names": record["employer_names"]
            }
        return None

@function_tool
def is_customer_linked_to_hot_property(customer_id: str) -> dict:
    """
    Check if a customer is linked to a "hot property" (address shared with more than 20 other customers).
    
    Args:
        customer_id (str): The ID of the customer to check.
    
    Returns:
        dict: Customer details, address information, and count of other customers at the same address.
              Returns None if customer is not linked to a hot property.
    """
    logger.info(f"TOOL: IS_CUSTOMER_LINKED_TO_HOT_PROPERTY - {customer_id}")
    with driver.session() as session:
        result = session.run(
            """
            MATCH (c:Customer {id: $customer_id})-[:LIVES_AT]->(a:Address)
            WITH a, c
            // find all other customers at that address
            MATCH (a)<-[:LIVES_AT]-(other:Customer)
            WHERE other <> c
            WITH a, c, count(other) AS num_other_customers
            WHERE num_other_customers > 20
            RETURN
              a.name         AS address,
              a.city         AS city,
              num_other_customers,
              c.name              AS customer_name,
              c.on_watchlist              AS customer_on_watchlist,
              c.is_pep              AS customer_is_pep
            """,
            customer_id=customer_id
        )
        record = result.single()
        if record is not None:
            return {
                "address": record["address"],
                "city": record["city"],
                "num_other_customers": record["num_other_customers"],
                "customer_name": record["customer_name"],
                "customer_on_watchlist": record["customer_on_watchlist"],
                "customer_is_pep": record["customer_is_pep"]
            }
        return None

async def main():
    
    
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
        tools=[get_customer_info,find_customers_in_rings, is_customer_in_suspicious_ring, is_customer_bridge, is_customer_linked_to_hot_property]
       
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

    

if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        # Ensure we clean up any remaining resources
        driver.close() 
        