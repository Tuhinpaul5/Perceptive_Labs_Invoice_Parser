from pathlib import Path
from pypdf import PdfReader
from datetime import datetime
import re
import json

pdf_dir = Path("pdfs")
timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
out_dir = Path("outputs") / f"{timestamp}_output"
out_dir.mkdir(parents=True, exist_ok=True)

def extract_company_name(text, keywords):
    """Extract first occurrence of Private/Public/Industries Limited after keywords"""
    pattern = r'(?:^|\n)([^\n]*?(?:Private|Public|Industries)\s+Limited[^\n]*?)(?:\n|$)'
    
    for keyword in keywords:
        idx = text.lower().find(keyword.lower())
        if idx != -1:
            search_text = text[idx:idx+500]
            match = re.search(pattern, search_text, re.IGNORECASE | re.MULTILINE)
            if match:
                return match.group(1).strip()
    return None

def extract_all_companies(text):
    """Extract all Private/Public/Industries Limited companies in order"""
    # Pattern 1: Private/Public/Industries Limited
    pattern1 = r'([^\n]*?(?:Private|Public|Industries)\s+Limited[^\n]*?)(?:\n|$)'
    # Pattern 2: Just Limited
    pattern2 = r'([A-Z][A-Z\s]+LIMITED)(?:\n|$)'
    
    companies = []
    seen = set()
    
    # First pass: get Private/Public/Industries Limited
    matches = re.finditer(pattern1, text, re.IGNORECASE | re.MULTILINE)
    for match in matches:
        company = match.group(1).strip()
        company_clean = company.lower()
        if company_clean not in seen and len(company) > 10:
            companies.append(company)
            seen.add(company_clean)
    
    # Second pass: get other LIMITED companies (must be all caps with multiple words)
    matches = re.finditer(pattern2, text, re.MULTILINE)
    for match in matches:
        company = match.group(1).strip()
        company_clean = company.lower()
        # Must have at least 2 words and end with LIMITED
        if company_clean not in seen and len(company.split()) >= 2 and company.endswith('LIMITED'):
            companies.append(company)
            seen.add(company_clean)
    
    return companies

def find_buyer_company(text, all_companies):
    """Find buyer company near Consignee/Consignor/Billed To/Shipped To keywords"""
    buyer_keywords = ['consignee', 'consignor', 'billed to', 'shipped to', 'buyer', 'customer', 'to,']
    
    for keyword in buyer_keywords:
        idx = text.lower().find(keyword)
        if idx != -1:
            # Search for company within 500 chars after keyword
            search_text = text[idx:idx+500]
            # Try both patterns
            pattern1 = r'([^\n]*?(?:Private|Public|Industries)\s+Limited[^\n]*?)(?:\n|$)'
            pattern2 = r'([A-Z][A-Z\s]+LIMITED)(?:\n|$)'
            
            match = re.search(pattern1, search_text, re.IGNORECASE | re.MULTILINE)
            if not match:
                match = re.search(pattern2, search_text, re.MULTILINE)
            
            if match:
                company = match.group(1).strip()
                # Make sure it's in our list of all companies
                for comp in all_companies:
                    if comp.lower() == company.lower():
                        return comp
    
    # Fallback to second company in list
    return None

def extract_address(text, company_name, excluded_addresses=None, occurrence=1):
    """Extract address near the company name occurrence, excluding already used addresses"""
    if not company_name:
        return None
    
    if excluded_addresses is None:
        excluded_addresses = []
    
    # Find the Nth occurrence of company name
    idx = -1
    for _ in range(occurrence):
        idx = text.find(company_name, idx + 1)
        if idx == -1:
            return None
    
    # Search both before and after the company name
    before_text = text[max(0, idx - 400):idx]
    after_text = text[idx + len(company_name):idx + len(company_name) + 400]
    
    # Look for address patterns (with Works:, Plot, addresses with pins)
    address_patterns = [
        r'(?:Works|Address|Office)\s*:\s*([^\n]+(?:\n[^\n]+){0,3})',
        r'(Plot[^\n]+(?:\n[^\n]+){0,2})',
        r'([A-Z0-9][^\n]+(?:Nagar|Road|Street|Building|Pin)[^\n]+(?:\n[^\n]+){0,2})'
    ]
    
    # Try to find address before company name first (common in invoices)
    for pattern in address_patterns:
        match = re.search(pattern, before_text, re.IGNORECASE)
        if match:
            address = match.group(1).strip()
            # Clean up and combine lines
            lines = [line.strip() for line in address.split('\n') if line.strip()]
            # Remove company names from address lines
            lines = [line for line in lines if not any(comp.lower() in line.lower() for comp in [company_name] if comp)]
            address = ' '.join(lines)
            
            # Check if similar to excluded addresses
            if len(address) > 20:
                is_similar = False
                for excl in excluded_addresses:
                    if excl and (address in excl or excl in address or address == excl):
                        is_similar = True
                        break
                if not is_similar:
                    return address
    
    # Then try after
    lines = [line.strip() for line in after_text.split('\n') if line.strip()]
    address_lines = []
    for line in lines[:5]:
        # Stop at document metadata
        if any(term in line.lower() for term in ['invoice', 'bill', 'gstin no', 'date:', 'order no', 'qty', 'original for']):
            break
        # Skip company name lines
        if company_name.lower() not in line.lower():
            address_lines.append(line)
    
    result = ' '.join(address_lines).strip() if address_lines else None
    
    if result and len(result) > 20:
        is_similar = False
        for excl in excluded_addresses:
            if excl and (result in excl or excl in result or result == excl):
                is_similar = True
                break
        if not is_similar:
            return result
    
    return None

def extract_vehicle_vessel(text):
    """Extract vehicle/vessel number"""
    patterns = [
        r'(?:vehicle\s+no\.?|truck\s+no\.?|vessel\s+no\.?)\s*:?\s*([A-Z]{2}\d{2}[A-Z]{2}\d{4})',
        r'(?:vehicle|truck|vessel)\s*:?\s*([A-Z]{2}\d{2}[A-Z]{2}\d{4})',
        r'(?:^|\n)([A-Z]{2}\d{2}[A-Z]{2}\d{4})(?:\n|$)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            vehicle = match.group(1).strip()
            # Validate it's a vehicle number format (not other codes)
            if len(vehicle) == 10 and vehicle[:2].isalpha() and vehicle[2:4].isdigit():
                return vehicle
    
    return None

def extract_gst_numbers(text, company_name, excluded_gst=None):
    """Extract GST number associated with a company, excluding already used GST"""
    if not company_name:
        return None
    
    if excluded_gst is None:
        excluded_gst = []
    
    # GST format: 2 digits + 5 letters + 4 digits + 1 letter + 1 digit + 1 letter + 1 alphanumeric
    gst_pattern = r'\b(\d{2}[A-Z]{5}\d{4}[A-Z]\d[A-Z][0-9A-Z])\b'
    
    # Find company position
    idx = text.find(company_name)
    if idx != -1:
        # Search within 500 chars before and after company name
        search_text = text[max(0, idx - 500):idx + 500]
        matches = re.finditer(gst_pattern, search_text)
        for match in matches:
            gst = match.group(1)
            if gst not in excluded_gst:
                return gst
    
    return None

# Loops through all the pdfs in the pdfs folder
for pdf_path in sorted(pdf_dir.glob("*.pdf")):
    txt_path = out_dir / pdf_path.with_suffix(".txt").name
    json_path = out_dir / pdf_path.with_suffix(".json").name

    try:
        reader = PdfReader(pdf_path)
        # Convert PDF to text
        text = "\n\n".join(page.extract_text() or "" for page in reader.pages)
        # Save text to file
        txt_path.write_text(text, encoding="utf-8")
        
        # Parse structured data
        companies = extract_all_companies(text)
        seller = companies[0] if len(companies) > 0 else None
        
        # Finds buyer using consignee/consignor pattern
        buyer = find_buyer_company(text, companies)
        if not buyer and len(companies) > 1:
            # Fallback to second company
            buyer = companies[1]
        
        # Validates seller: check if seller name appears in "Authorised Signatory" section
        # If not, they might be swapped
        auth_sig_idx = text.lower().find('authorised signatory')
        if auth_sig_idx != -1 and seller:
            # Check 200 chars around "Authorised Signatory"
            auth_section = text[max(0, auth_sig_idx - 100):auth_sig_idx + 200]
            if seller.lower() not in auth_section.lower():
                # Seller not in auth section, likely swapped - check if buyer is there
                if buyer and buyer.lower() in auth_section.lower():
                    # Swap them
                    seller, buyer = buyer, seller
        
        # Extracts seller address first (from first occurrence)
        seller_address = extract_address(text, seller, occurrence=1)
        seller_gst = extract_gst_numbers(text, seller)
        
        # Extracts buyer address from first occurrence, excluding seller address
        buyer_address = extract_address(text, buyer, excluded_addresses=[seller_address] if seller_address else [], occurrence=1)
        buyer_gst = extract_gst_numbers(text, buyer, excluded_gst=[seller_gst] if seller_gst else [])
        
        # If buyer address is still similar or None, try second occurrence of buyer
        if not buyer_address:
            buyer_address = extract_address(text, buyer, excluded_addresses=[seller_address] if seller_address else [], occurrence=2)
        
        parsed_data = {
            "seller": seller,
            "seller_address": seller_address,
            "buyer": buyer,
            "buyer_address": buyer_address,
            "gst": buyer_gst,
            "vehicle_vessel_number": extract_vehicle_vessel(text),
            "description": text[:200].replace('\n', ' ').strip() + "..."
        }
        
        json_path.write_text(json.dumps(parsed_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Processed {pdf_path.name}: {len(reader.pages)} pages")
        
    except Exception as e:
        print(f"Failed {pdf_path.name}: {e}")