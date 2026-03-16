import sqlite3
import os

# Absolute path based on CWD of the app
DB_PATH = "apps/api-gateway/agenthub_data/agenthub.db"

def migrate():
    # Attempt to find the DB globally if relative fails
    if not os.path.exists(DB_PATH):
        # Try root relative
        potential_path = os.path.join(os.getcwd(), DB_PATH)
        if not os.path.exists(potential_path):
            print(f"Database not found at {DB_PATH}. Please run from project root.")
            return
        db_file = potential_path
    else:
        db_file = DB_PATH

    conn = sqlite3.connect(db_file)
    cursor = conn.cursor()

    print(f"Starting migration for {db_file}...")

    # Columns to add to 'bounty'
    bounty_cols = [
        ("dependencies", "JSON"),
        ("estimated_hours", "INTEGER"),
        ("track", "TEXT"),
        ("is_temporary_claim", "BOOLEAN DEFAULT 0"),
        ("claim_expires_at", "DATETIME"),
        ("claimed_by_user_id", "TEXT")
    ]

    # Columns to add to 'commitrecord'
    commit_cols = [
        ("blackbox_status", "TEXT DEFAULT 'pending'"),
        ("blackbox_report", "JSON")
    ]

    def add_column(table, col_name, col_type):
        try:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
            print(f"✅ Added column {col_name} to {table}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                print(f"ℹ️ Column {col_name} already exists in {table}, skipping.")
            else:
                print(f"❌ Error adding {col_name} to {table}: {e}")

    for col, ctype in bounty_cols:
        add_column("bounty", col, ctype)

    for col, ctype in commit_cols:
        add_column("commitrecord", col, ctype)

    conn.commit()
    conn.close()
    print("Migration completed.")

if __name__ == "__main__":
    migrate()
