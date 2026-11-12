"""Domain accounting exceptions."""


class AccountingError(Exception):
    """Base exception for accounting domain errors."""


class InsufficientStockError(AccountingError):
    """Raised when a sale transaction requests more quantity than currently available in stock."""


class UnbalancedJournalError(AccountingError):
    """Raised when total debits do not equal total credits in a journal entry."""


class MissingPriceError(AccountingError):
    """Raised when a required purchase or sell price is missing."""


class InvalidTransactionStatusError(AccountingError):
    """Raised when attempting to execute accounting logic on an unconfirmed transaction."""
