import sqlite3

def add_indexes():
    conn = sqlite3.connect('combined_data.db')
    cursor = conn.cursor()
    
    print("Adding database indexes for performance...")
    
    queries = [
        "CREATE INDEX IF NOT EXISTS idx_date ON sales_data(Date)",
        "CREATE INDEX IF NOT EXISTS idx_branch ON sales_data(Branch)",
        "CREATE INDEX IF NOT EXISTS idx_staff ON sales_data(Staff)",
        "CREATE INDEX IF NOT EXISTS idx_customer ON sales_data([Customer Mobile])",
        "CREATE INDEX IF NOT EXISTS idx_rbm ON sales_data(RBM)",
        "CREATE INDEX IF NOT EXISTS idx_bdm ON sales_data(BDM)"
    ]
    
    for q in queries:
        print(f"Executing: {q}")
        cursor.execute(q)
    
    conn.commit()
    conn.close()
    print("Indexing complete.")

if __name__ == "__main__":
    add_indexes()
