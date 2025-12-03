import streamlit as st
import pandas as pd
import boto3
import io
import re
from datetime import datetime, timedelta
import random
import os


# Do NOT hardcode credentials — use environment variables
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
BUCKET_NAME = "medchain"

# Create S3 client only if keys exist
if AWS_ACCESS_KEY and AWS_SECRET_KEY:
    s3_client = boto3.client(
        "s3",
        aws_access_key_id=AWS_ACCESS_KEY,
        aws_secret_access_key=AWS_SECRET_KEY
    )
else:
    s3_client = None

# -----------------------------
# GTIN Generator
# -----------------------------
def generate_valid_gtin():
    body = ''.join([str(random.randint(0, 9)) for _ in range(13)])
    digits = [int(d) for d in body]
    check_digit = (10 - (sum((3 if i % 2 == 0 else 1) * d for i, d in enumerate(digits)) % 10)) % 10
    return body + str(check_digit)

# -----------------------------
# Sample Data Generator
# -----------------------------
def generate_sample_data(n=100):
    ports = ["Nhava Sheva", "Kolkata", "Mumbai", "Chennai", "Delhi"]
    descriptions = ["Paracetamol 500mg", "Amoxicillin 250mg", "Ibuprofen 400mg", "Ciprofloxacin 250mg", "Azithromycin 500mg"]
    uqcs = ["BOT", "NOS", "TAB", "CAP", "INJ"]
    dest_ports = ["London", "Singapore", "Durban", "Nairobi", "New York"]
    countries = ["UK", "Kenya", "South Africa", "USA", "Singapore"]
    valid_cth_codes = ["30041020", "30049085", "30043919", "30049099", "30042019"]

    data = []
    for _ in range(n):
        row = {
            "GTIN": generate_valid_gtin(),
            "Date": (datetime.today() - timedelta(days=random.randint(0, 10))).strftime('%Y-%m-%d'),
            "Indian Port": random.choice(ports),
            "CTH": random.choice(valid_cth_codes),
            "Description": random.choice(descriptions),
            "Quantity": random.randint(1000, 10000),
            "UQC": random.choice(uqcs),
            "Total value USD": random.randint(500, 5000),
            "Destination Port": random.choice(dest_ports),
            "Country": random.choice(countries)
        }
        data.append(row)
    return pd.DataFrame(data)

# -----------------------------
# GTIN Validation
# -----------------------------
def validate_gtin(gtin):
    if not gtin or not re.match(r'^\d{8,14}$', str(gtin)):
        return False
    digits = [int(d) for d in str(gtin)]
    check_digit = digits[-1]
    weights = [3 if i % 2 == 0 else 1 for i in range(len(digits) - 1)]
    weighted_sum = sum(d * w for d, w in zip(digits[:-1], weights[::-1]))
    calculated_check = (10 - (weighted_sum % 10)) % 10
    return calculated_check == check_digit

# -----------------------------
# CTH Validation
# -----------------------------
def validate_cth(cth):
    valid_cth_codes = ["30041020", "30049085", "30043919", "30049099", "30042019"]
    return str(cth) in valid_cth_codes

# -----------------------------
# UQC Validation
# -----------------------------
def validate_uqc(uqc):
    valid_uqcs = ["BOT", "NOS", "TAB", "CAP", "INJ"]
    return str(uqc) in valid_uqcs

# -----------------------------
# Shipment Delay Detection
# -----------------------------
def detect_shipment_delays(df, delay_threshold_days=2):
    issues = []
    summary = {'total_shipments': len(df), 'delayed_shipments': 0, 'max_delay_days': 0}
    df['Delay_Days'] = 0
    df['Delay_Severity'] = 'None'

    if 'Date' not in df.columns:
        issues.append("Date column missing; shipment delay checks skipped.")
        return df, issues, summary

    df['Date'] = pd.to_datetime(df['Date'], errors='coerce')
    current_date = datetime.now()
    delayed_mask = df['Date'] < current_date - timedelta(days=delay_threshold_days)

    df.loc[delayed_mask, 'Validation_Status'] = 'Invalid'
    df.loc[delayed_mask, 'Issues'] += 'Shipment delayed; '
    df.loc[delayed_mask, 'Delay_Days'] = (current_date - df['Date']).dt.days

    df.loc[delayed_mask & (df['Delay_Days'] <= 3), 'Delay_Severity'] = 'Minor'
    df.loc[delayed_mask & (df['Delay_Days'] > 3) & (df['Delay_Days'] <= 7), 'Delay_Severity'] = 'Moderate'
    df.loc[delayed_mask & (df['Delay_Days'] > 7), 'Delay_Severity'] = 'Critical'

    summary['delayed_shipments'] = delayed_mask.sum()
    if delayed_mask.any():
        summary['max_delay_days'] = df.loc[delayed_mask, 'Delay_Days'].max()

    if summary['delayed_shipments'] > 0:
        issues.append(f"Detected {summary['delayed_shipments']} delayed shipments.")

    return df, issues, summary

# -----------------------------
# Data Validation
# -----------------------------
def validate_data(df, delay_threshold_days=2):
    issues = []
    df['Validation_Status'] = 'Valid'
    df['Issues'] = ''

    expected_columns = ['GTIN', 'Date', 'Indian Port', 'CTH', 'Description',
                        'Quantity', 'UQC', 'Total value USD', 'Destination Port', 'Country']

    for col in expected_columns:
        if col not in df.columns:
            issues.append(f"Missing column: {col}")
            df[col] = pd.NA
            df['Validation_Status'] = 'Invalid'
            df['Issues'] += f'Missing {col}; '

    for col in expected_columns:
        df.loc[df[col].isna(), 'Validation_Status'] = 'Invalid'
        df.loc[df[col].isna(), 'Issues'] += f'Missing {col} value; '

    df['GTIN_Valid'] = df['GTIN'].apply(validate_gtin)
    df.loc[~df['GTIN_Valid'], 'Validation_Status'] = 'Invalid'
    df.loc[~df['GTIN_Valid'], 'Issues'] += 'Invalid GTIN; '

    df['CTH_Valid'] = df['CTH'].apply(validate_cth)
    df.loc[~df['CTH_Valid'], 'Validation_Status'] = 'Invalid'
    df.loc[~df['CTH_Valid'], 'Issues'] += 'Invalid CTH code; '

    df['UQC_Valid'] = df['UQC'].apply(validate_uqc)
    df.loc[~df['UQC_Valid'], 'Validation_Status'] = 'Invalid'
    df.loc[~df['UQC_Valid'], 'Issues'] += 'Invalid UQC code; '

    duplicates = df[df.duplicated(subset=['GTIN'], keep=False)]
    if not duplicates.empty:
        df.loc[df['GTIN'].isin(duplicates['GTIN']), 'Validation_Status'] = 'Invalid'
        df.loc[df['GTIN'].isin(duplicates['GTIN']), 'Issues'] += 'Duplicate GTIN; '
        issues.append(f"Detected {len(duplicates)} duplicate GTINs")

    df, delay_issues, delay_summary = detect_shipment_delays(df, delay_threshold_days)
    issues.extend(delay_issues)

    return df, issues, delay_summary

# -----------------------------
# Upload to S3 (only if configured)
# -----------------------------
def upload_to_s3(df, filename):
    if s3_client is None:
        return "Cloud upload disabled. No AWS credentials provided."

    csv_buffer = io.StringIO()
    df.to_csv(csv_buffer, index=False)
    s3_client.put_object(Bucket=BUCKET_NAME, Key=filename, Body=csv_buffer.getvalue())
    return f"Data uploaded to s3://{BUCKET_NAME}/{filename}"

# -----------------------------
# GS1 Chatbot
# -----------------------------
def gs1_chatbot(query, df, delay_summary):
    query = query.lower()

    if "delay" in query:
        return (f"📦 Shipment Delay Analysis:\n"
                f"- Total Shipments: {delay_summary['total_shipments']}\n"
                f"- Delayed Shipments: {delay_summary['delayed_shipments']}\n"
                f"- Maximum Delay: {delay_summary['max_delay_days']} days")

    match = re.search(r'gtin\s*(\d+)', query)
    if match and not df.empty:
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(df):
            row = df.iloc[idx]
            gtin = str(row.get('GTIN', ''))
            desc = str(row.get('Description', 'Unknown'))
            status = row.get('Validation_Status', 'Unknown')
            issues = row.get('Issues', 'None')
            return (f"📄 **GTIN {gtin}** for **'{desc}'**\n"
                    f"- GS1 Valid: {validate_gtin(gtin)}\n"
                    f"- Status: {status}\n"
                    f"- Issues: {issues or 'None'}")

    return "🤖 Try: 'Check GTIN 1', 'Is GTIN valid?', or 'Delayed shipments'."

# -----------------------------
# Streamlit App
# -----------------------------
st.set_page_config(page_title="MedChain Dashboard", layout="wide")
st.title("MedChain: Pharmaceutical Supply Chain Integrity Dashboard")

st.header("Data Upload")
uploaded_file = st.file_uploader("Upload CSV or Excel", type=['csv', 'xlsx'])

if uploaded_file:
    try:
        df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith('.csv') else pd.read_excel(uploaded_file)
    except:
        st.error("Error reading file. Using sample dataset.")
        df = generate_sample_data()
else:
    st.info("Using sample dataset (100 rows).")
    df = generate_sample_data()

st.header("Validation Results")
threshold = st.slider("Delay Threshold (days)", 1, 30, 2)
validated_df, issues, summary = validate_data(df, delay_threshold_days=threshold)

if issues:
    st.warning("Issues Found:")
    for i in issues:
        st.markdown(f"- {i}")
else:
    st.success("No issues detected.")

st.dataframe(validated_df, use_container_width=True)

st.header("Export / Cloud Storage")
col1, col2 = st.columns(2)

with col1:
    if st.button("Upload to AWS S3"):
        filename = f"validated_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        msg = upload_to_s3(validated_df, filename)
        st.success(msg)

with col2:
    csv_buf = io.StringIO()
    validated_df.to_csv(csv_buf, index=False)
    st.download_button("Download CSV", csv_buf.getvalue(), "validated_report.csv", "text/csv")

st.header("GS1 Assistant")
query = st.text_input("Ask about GTIN, delays, etc.")
if query:
    st.markdown(gs1_chatbot(query, validated_df, summary))
