"""Action: the GraphQL queries the web app sends (reconstructed from its JavaScript).

Action uses "automatic persisted queries": the first request only carries the hash of a
query and, if the server does not know the hash (yet, or anymore), answers
PersistedQueryNotFound. The client must then send the full query once (POST). Without
that, receipts only work while the website happens to have registered the query.
The SHA-256 of each text below equals the hash the website uses (checked).
"""
from __future__ import annotations

from hashlib import sha256

RECEIPT_LIST = '''query ReceiptList($limit: Int, $offset: String) {
  receiptList(limit: $limit, offset: $offset) {
    receipts {
      id
      dateTime
      dateTimeFormat
      store {
        name
        id
      }
      price {
        currency
        total
      }
      returningPeriod {
        lastReturnDate
        returnable
      }
    }
    hasNext
    offset
  }
}'''

RECEIPT_DETAILS = '''query ReceiptDetails($receiptId: String!) {
  receipt(id: $receiptId) {
    id
    receiptNumber
    dateTimeFormat
    store {
      id
      name
      address
    }
    returningPeriod {
      totalDays
      daysLeft
      remainingDays
      returnable
      lastReturnDate
      lastReturnDateFormat
    }
    price {
      total
      subTotal
      employeeDiscount
      otherDiscounts
      currency
      format {
        total
        subTotal
        employeeDiscount
        otherDiscounts
      }
      vat {
        format {
          perPercentage {
            percentage
            specification {
              vatAmount
              totalIncludingVat
              totalExcludingVat
            }
          }
          total {
            vatAmount
            totalIncludingVat
            totalExcludingVat
          }
        }
      }
    }
    totalQuantity
    barcode
    qrCode
    dateTime
    barcodeBase64Svg
    products {
      code
      description
      totalPriceFormat
      quantity
      price {
        adjustedSalesPrice
        regularSalesPrice
      }
    }
    paymentMethods
    membershipId
    warrantyYears
  }
}'''

QUERIES = {"ReceiptList": RECEIPT_LIST, "ReceiptDetails": RECEIPT_DETAILS}
HASHES = {name: sha256(text.encode()).hexdigest() for name, text in QUERIES.items()}
