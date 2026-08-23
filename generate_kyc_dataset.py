import os
from neo4j import GraphDatabase
from dotenv import load_dotenv
import random
import numpy as np
import time
import uuid
from datetime import datetime, timedelta

# reads .env into os.environ
load_dotenv()  

# Seed the RNG—use any constant (e.g. 42)
random.seed(42)

# ———————————————
# 0. Neo4j Aura Connection Setup
# ———————————————
# (Set these in your shell or CI/CD pipeline—never check secrets into Git!)
# Read Neo4j environment variables into variables
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "password")
NEO4J_DATABASE = os.getenv("NEO4J_DATABASE", "neo4j")

# 3. (Optional) Debug print — remove or comment out before committing!
print(f"Using URI={NEO4J_URI}")
print(f"Using USER={NEO4J_USER}")
print(f"Using DATABASE={NEO4J_DATABASE}")


# By using the neo4j+s:// scheme, encryption & certificate trust are automatic
driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# When you open sessions you may want to specify the default database (usually "neo4j"):
def get_session():
    return driver.session(database=NEO4J_DATABASE)


# ———————————————
# 1. Create uniqueness constraints (once)
# ———————————————
with get_session() as sess:
    
    for label in ('Customer','Account','Company','Address',
                  'Device','IP_Address','Payment_Method','Transaction'):
        sess.execute_write(
            lambda tx, L=label: tx.run(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{L}) REQUIRE n.id IS UNIQUE"
            )
        )


# ———————————————
# 2. Configuration & ID lists
# ———————————————
random.seed(42)
np.random.seed(42)

n_customers = 8_000
mean_accounts_per_customer   = 1.5
mean_devices_per_customer    = 2
mean_addresses_per_customer  = 1.2
mean_payment_methods_per_customer = 1
mean_transactions_per_account     = 10
p_pep       = 0.01
p_watchlist = 0.02

# IDs
customers   = [f"CUST_{i:05d}" for i in range(1, n_customers+1)]
n_companies = int(n_customers * 0.2)
companies   = [f"COMP_{i:05d}" for i in range(1, n_companies+1)]


# Prepare payloads
customer_rows = [
    {"id": cust,
     "pep": (random.random() < p_pep),
     "wl":  (random.random() < p_watchlist),
     "name": cust
    }
    for cust in customers
]

company_rows = [
    {"id": comp,
     "ind": random.choice(['Finance','Tech','Manufacturing','Retail']),
     "name": comp
     }
    for comp in companies
]

# ———————————————
# 3. Push Customers & Companies
# ———————————————

print(f"loading start...")
start_time = time.perf_counter()

batch_size=50

with get_session() as sess:
    # Customers in implicit transactions of 50 rows each
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MERGE (c:Customer {id: row.id})
          SET c.is_pep       = row.pep,
              c.on_watchlist = row.wl,
              c.name = row.name
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=customer_rows,
        batch_size=batch_size
    )

    # Companies in implicit transactions of 50 rows each
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MERGE (c:Company {id: row.id})
          SET c.industry = row.ind,
              c.name = row.name 
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=company_rows,
        batch_size=batch_size
    )

    
end_time = time.perf_counter()
elapsed = end_time - start_time
print(f"⌛ Loading Customers & Companies took {elapsed:.2f} seconds")



# ———————————————
# 3. Push Accounts, Addresses, Devices, IP addresses, Payment Methods and Transactions
# ———————————————

# Build payloads
acct_counter = addr_counter = dev_counter = ip_counter = pm_counter = txn_counter = 0

account_rows     = []
employed_rows    = []
address_rows     = []
device_rows      = []
ip_rows          = []
payment_rows     = []
transaction_rows = []


# 1.1 Accounts & OWNS
for cust in customers:
    for _ in range(np.random.poisson(mean_accounts_per_customer)):
        acct_counter += 1
        aid = f"ACCT_{acct_counter:05d}"
        account_rows.append({"cust": cust, "acct": aid,"name":aid})

# 1.2 EMPLOYED_BY
for cust in customers:
    if random.random() < 0.8:
        comp = random.choice(companies)
        employed_rows.append({"cust": cust, "co": comp})

# 1.3 Addresses & LIVES_AT
for cust in customers:
    for _ in range(max(1, np.random.poisson(mean_addresses_per_customer))):
        addr_counter += 1
        aid = f"ADDR_{addr_counter:05d}"
        city = random.choice(['London','Manchester','Birmingham','Leeds'])
        address_rows.append({"cust": cust, "addr": aid, "city": city,"name":aid})

# 1.4 Devices & USES_DEVICE → ASSOCIATED_WITH IP_Address
for cust in customers:
    for _ in range(np.random.poisson(mean_devices_per_customer)):
        dev_counter += 1
        did = f"DEV_{dev_counter:05d}"
        osys = random.choice(['Android','iOS','Windows','MacOS'])
        device_rows.append({"cust": cust, "dev": did, "os": osys,"name":did})

        ip_counter += 1
        iid = f"IP_{ip_counter:05d}"
        ip_rows.append({"dev": did, "ip": iid,"name":iid})

# 1.5 Payment Methods & HAS_METHOD
for cust in customers:
    for _ in range(np.random.poisson(mean_payment_methods_per_customer)):
        pm_counter += 1
        pid = f"PM_{pm_counter:05d}"
        ptype = random.choice(['Credit_Card','Debit_Card','EWallet'])
        cnum = ''.join(random.choice('0123456789') for _ in range(16)) \
               if ptype in ('Credit_Card','Debit_Card') \
               else uuid.uuid4().hex[:16]
        payment_rows.append({
            "cust": cust,
            "pid": pid,
            "ptype": ptype,
            "cnum": cnum,
            "name": pid
        })


# 1.6 Transactions & FROM/TO
all_accts = [r["acct"] for r in account_rows]
for src in all_accts:
    for _ in range(np.random.poisson(mean_transactions_per_account)):
        txn_counter += 1
        tid = f"TXN_{txn_counter:06d}"
        amt = round(np.random.lognormal(mean=3, sigma=1), 2)
        ts  = (datetime(2025,1,1) + timedelta(days=random.randint(0,120))).isoformat()
        dst = random.choice(all_accts)
        transaction_rows.append({
            "src": src, "tid": tid, "amt": amt, "ts": ts, "dst": dst, "name":tid
        })


# 2. Push in batches
start_time = time.perf_counter()
with get_session() as sess:
    # 2.1 Accounts
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MERGE (a:Account {id: row.acct})
          SET a.name = row.name
          WITH a, row
          MATCH (c:Customer {id: row.cust})
          MERGE (c)-[:OWNS]->(a)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=account_rows, batch_size=batch_size
    )

    # 2.2 Employed
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MATCH (c:Customer {id: row.cust})
          MATCH (co:Company  {id: row.co})
          MERGE (c)-[:EMPLOYED_BY]->(co)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=employed_rows, batch_size=batch_size
    )

    # 2.3 Addresses
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MERGE (a:Address {id: row.addr})
          SET a.city = row.city,
              a.name = row.name
          WITH a, row
          MATCH (c:Customer {id: row.cust})
          MERGE (c)-[:LIVES_AT]->(a)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=address_rows, batch_size=batch_size
    )

    # 2.4 Devices
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MERGE (d:Device {id: row.dev})
          SET d.os = row.os,
            d.name = row.name
          WITH d, row
          MATCH (c:Customer {id: row.cust})
          MERGE (c)-[:USES_DEVICE]->(d)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=device_rows, batch_size=batch_size
    )
    # 2.5 IPs
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MERGE (i:IP_Address {id: row.ip})
          SET i.name = row.name
          WITH i, row
          MATCH (d:Device {id: row.dev})
          MERGE (d)-[:ASSOCIATED_WITH]->(i)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=ip_rows, batch_size=batch_size
    )

    # 2.6 Payment Methods
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MERGE (p:Payment_Method {id: row.pid})
          SET p.pm_type     = row.ptype,
              p.card_number = row.cnum,
              p.name = row.name
          WITH p, row
          MATCH (c:Customer {id: row.cust})
          MERGE (c)-[:HAS_METHOD]->(p)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=payment_rows, batch_size=batch_size
    )

    # 2.7 Transactions
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MERGE (t:Transaction {id: row.tid})
          SET t.amount    = row.amt,
              t.timestamp = row.ts,
              t.name = row.name
          WITH t, row
          MATCH (a1:Account {id: row.src})
          MATCH (a2:Account {id: row.dst})
          MERGE (a1)-[:FROM]->(t)-[:TO]->(a2)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=transaction_rows, batch_size=batch_size
    )

end_time = time.perf_counter()
elapsed = end_time - start_time
print(f"⌛ Loading Account, Employed, Owns, Addresses, Devices, Payment Methods & Transactions took {elapsed:.2f} seconds")


# 5. Select anomalies
n_anomalies        = int(0.05 * len(customers))
anoms             = random.sample(customers, n_anomalies)
chunk             = n_anomalies // 5

# Prepare payload lists
super_rows        = []
ring_acct_rows    = []
ring_txn_rows     = []
bridge_rows       = []
isolate_rows      = []
dense_addr_rows   = []
dense_pm_rows     = []


# Super-hubs: 50 new accounts per customer
for cust in anoms[0:chunk]:
    for _ in range(50):
        acct_counter += 1
        aid = f"ACCT_{acct_counter:05d}"
        super_rows.append({"cust": cust, "acct": aid,"name":aid})



#Upload
with get_session() as sess:
    # 4.1 Super-hubs
    start_time = time.perf_counter()
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MERGE (a:Account {id: row.acct})
          SET a.name = row.name
          WITH a, row
          MATCH (c:Customer {id: row.cust})
          MERGE (c)-[:OWNS]->(a)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=super_rows, batch_size=50
    )
    end_time = time.perf_counter()
    elapsed = end_time - start_time
    print(f"⌛ Loading Anomalies: Super Hubs took {elapsed:.2f} seconds")

# 2.2 Circular rings: 3-customer cycles
for i in range(chunk, 2*chunk, 3):
    trio = anoms[i : i+3]
    if len(trio) == 3:
        accts = []
        for c in trio:
            acct_counter += 1
            aid = f"ACCT_{acct_counter:05d}"
            ring_acct_rows.append({"cust": c, "acct": aid})
            accts.append(aid)
        for j in range(3):
            txn_counter += 1
            tid = f"TXN_{txn_counter:06d}"
            ring_txn_rows.append({
                "src":      accts[j],
                "dst":      accts[(j+1) % 3],
                "tid":      tid,
                "amount":   1000,
                "ts":       datetime(2025, 2, 1).isoformat()
            })

with get_session() as sess:
    # 4.2 Circular rings – ring transactions
    start_time = time.perf_counter()
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MERGE (t:Transaction {id: row.tid})
          SET t.amount = row.amount, t.timestamp = row.ts
          WITH t, row
          MATCH (a1:Account {id: row.src}), (a2:Account {id: row.dst})
          MERGE (a1)-[:FROM]->(t)-[:TO]->(a2)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=ring_txn_rows, batch_size=50
    )
    end_time = time.perf_counter()
    elapsed = end_time - start_time
    print(f"⌛ Loading Anomalies: Circular Rings took {elapsed:.2f} seconds")


# 2.3 Bridges: employed by two companies
for cust in anoms[2*chunk : 3*chunk]:
    c1, c2 = random.sample(companies, 2)
    bridge_rows.append({"cust": cust, "co": c1})
    bridge_rows.append({"cust": cust, "co": c2})
with get_session() as sess:
    # 4.3 Bridges
    start_time = time.perf_counter()
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MATCH (c:Customer {id: row.cust}), (co:Company {id: row.co})
          MERGE (c)-[:EMPLOYED_BY]->(co)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=bridge_rows, batch_size=50
    )
    end_time = time.perf_counter()
    elapsed = end_time - start_time
    print(f"⌛ Loading Anomalies: Bridges - Customers employeed by 2 companies took {elapsed:.2f} seconds")

#  Isolates: 5 device→IP pairs per customer, no link to customers
for cust in anoms[3*chunk : 4*chunk]:
    for _ in range(5):
        dev_counter += 1
        ip_counter  += 1
        isolate_rows.append({
            "dev": f"DEV_{dev_counter:05d}",
            "ip":  f"IP_{ip_counter:05d}"
        })

with get_session() as sess:
    # 4.4 Isolates
    start_time = time.perf_counter()
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MERGE (d:Device {id: row.dev})
          SET d.os = 'Unknown'
          MERGE (i:IP_Address {id: row.ip})
          MERGE (d)-[:ASSOCIATED_WITH]->(i)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=isolate_rows, batch_size=50
    )
    end_time = time.perf_counter()
    elapsed = end_time - start_time
    print(f"⌛ Loading Anomalies: Isolated Devices and IP Addresses with no Customers took {elapsed:.2f} seconds")

# Dense watchlist cluster: shared address & payment method
shared_addr = f"ADDR_{addr_counter+1:05d}"
shared_pm   = f"PM_{pm_counter+1:05d}"
dense_addr_rows = [{"cust": cust, "addr": shared_addr}
                   for cust in anoms[4*chunk : ]]
dense_pm_rows   = [{"cust": cust, "pm":   shared_pm}
                   for cust in anoms[4*chunk : ]]

# 3. Create the two shared nodes up front
with get_session() as sess:
    sess.run(
        "MERGE (a:Address {id:$addr}) SET a.city='London', a.name=$addr",
        addr=shared_addr
    )
    sess.run(
        "MERGE (p:Payment_Method {id:$pm}) SET p.pm_type='Credit_Card', p.name=$pm",
        pm=shared_pm
    )
with get_session() as sess:
    start_time = time.perf_counter()
    # 4.5 Dense cluster – shared address
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MATCH (c:Customer {id: row.cust}), (a:Address {id: row.addr})
          MERGE (c)-[:LIVES_AT]->(a)
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=dense_addr_rows, batch_size=50
    )
    # 4.5 Dense cluster – shared payment method + watchlist flag
    sess.run(
        """
        UNWIND $rows AS row
        CALL (row) {
          MATCH (c:Customer {id: row.cust}), (p:Payment_Method {id: row.pm})
          MERGE (c)-[:HAS_METHOD]->(p)
          SET c.on_watchlist = true
        } IN TRANSACTIONS OF $batch_size ROWS
        """,
        rows=dense_pm_rows, batch_size=50
    )
    end_time = time.perf_counter()
    elapsed = end_time - start_time
    print(f"⌛ Loading Anomalies: Dense clusters - Around shared address & payment method took {elapsed:.2f} seconds")
