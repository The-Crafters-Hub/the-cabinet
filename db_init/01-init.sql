-- 🏗️ The Crafters Hub: Initial Database Schema
-- Translated directly from the historical Notion Archive (Student_ID, Registration_ID, etc.)
-- 1. Services (Courses & Workshops pulled from Wix)
CREATE TABLE IF NOT EXISTS services (
    id SERIAL PRIMARY KEY,
    wix_id VARCHAR(100) UNIQUE,
    service_type VARCHAR(50) NOT NULL,
    -- 'COURSE' or 'WORKSHOP'
    title VARCHAR(255) NOT NULL,
    price NUMERIC(10, 2),
    status VARCHAR(50) DEFAULT 'ACTIVE',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- 2. Students (From Notion 'Student_ID' Table)
CREATE TABLE IF NOT EXISTS students (
    id SERIAL PRIMARY KEY,
    wix_contact_id VARCHAR(255) UNIQUE,
    notion_id VARCHAR(50) UNIQUE,
    full_name VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(50),
    status VARCHAR(50),
    total_spend DECIMAL(10, 2) DEFAULT 0.00,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE instructors (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    email VARCHAR(255),
    phone VARCHAR(50),
    bio TEXT,
    active BOOLEAN DEFAULT TRUE,
    team_type VARCHAR(50) -- 'TCH Team' or 'External'
);
-- 3. Registrations/Bookings (From Notion 'Registration_ID' Table - Foreign Keys!)
CREATE TABLE IF NOT EXISTS registrations (
    id SERIAL PRIMARY KEY,
    wix_booking_id VARCHAR(255) UNIQUE,
    notion_registration_id VARCHAR(50) UNIQUE,
    student_id INTEGER REFERENCES students(id),
    service_id INTEGER REFERENCES services(id),
    registration_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    amount_paid DECIMAL(10, 2),
    payment_method VARCHAR(50),
    status VARCHAR(50),
    notes TEXT
);
CREATE TABLE finance_transactions (
    id SERIAL PRIMARY KEY,
    transaction_date TIMESTAMP NOT NULL,
    invoice_number VARCHAR(50),
    description TEXT,
    category VARCHAR(100),
    sub_category VARCHAR(100),
    income_receivable DECIMAL(10, 2) DEFAULT 0,
    income_cash DECIMAL(10, 2) DEFAULT 0,
    income_bank DECIMAL(10, 2) DEFAULT 0,
    expense_payable DECIMAL(10, 2) DEFAULT 0,
    expense_cash DECIMAL(10, 2) DEFAULT 0,
    expense_bank DECIMAL(10, 2) DEFAULT 0,
    notes TEXT
);
-- 4. Marketing Spend (For n8n nighty pulls from Meta/Google)
CREATE TABLE IF NOT EXISTS marketing_spend (
    id SERIAL PRIMARY KEY,
    spend_date DATE NOT NULL,
    platform VARCHAR(50) NOT NULL,
    -- e.g., 'META', 'GOOGLE', 'TIKTOK'
    campaign_name VARCHAR(255),
    amount_spent NUMERIC(10, 2) NOT NULL,
    impressions INTEGER DEFAULT 0,
    clicks INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Create a view (Rollup) simulating the Notion rollups for CEO Dashboard
CREATE VIEW vw_student_LTV AS
SELECT s.id as student_id,
    s.full_name,
    COUNT(r.id) as total_courses_booked,
    SUM(r.amount_paid) as lifetime_value
FROM students s
    LEFT JOIN registrations r ON s.id = r.student_id
GROUP BY s.id,
    s.full_name;