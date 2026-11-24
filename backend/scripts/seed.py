"""Create a demo kiosk with 2 printers x 5 compartments and a demo user.

Run from the backend folder:  python -m scripts.seed
Prints the kiosk API key once; keep it for the kiosk agent (M2).
"""
from sqlalchemy import select

from app.db import SessionLocal
from app.main import init_db
from app.models import Kiosk, User
from app.security import hash_password
from app.services.kiosks import add_printer, create_kiosk


def main() -> None:
    init_db()
    with SessionLocal() as db:
        if db.scalar(select(Kiosk.id).where(Kiosk.name == "Demo kiosk")):
            print("demo data already exists")
            return
        kiosk, api_key = create_kiosk(db, "Demo kiosk", "Main building, ground floor")
        add_printer(db, kiosk.id, "Printer A", "printer_a", duplex=True, compartments=5)
        add_printer(db, kiosk.id, "Printer B", "printer_b", duplex=True, compartments=5)
        if not db.scalar(select(User.id).where(User.email == "demo@example.com")):
            db.add(User(email="demo@example.com", password_hash=hash_password("demo12345")))
        db.commit()
        print(f"kiosk id {kiosk.id}, API key (shown once): {api_key}")
        print("demo user: demo@example.com / demo12345")


if __name__ == "__main__":
    main()
