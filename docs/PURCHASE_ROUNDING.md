# Configurable purchase rounding

This feature is opt-in. Installing or migrating creates configuration fields with
activation **off** on every Currency. It does not alter System Settings, Accounts
Settings, currency formats, submitted transactions, tax templates or existing scripts.
No new app is required; this module extends the existing IGCTools app.

## Configuration after deployment and staging verification

Open **Currency / Moneda**, choose a currency and expand **Redondeo en compras**.

| Field | Suggested local-currency setting | Existing USD behavior |
|---|---|---|
| Activate purchase policy | Enabled after testing | Disabled |
| Unit-price decimals | 4 | Native 4 |
| Item-amount decimals | 2 | Native 4 |
| Tax decimals | 2 | Native 4 |
| Total decimals | 2 | Native 4 |
| Method | Commercial Rounding | Native system method |
| Tax calculation | Por subtotal | Native account setting |

The calculator contains no currency-code or supplier-name conditions. These
settings work for any enabled currency. Precision accepts 0–9; total precision
cannot be lower than line-amount or tax precision, to avoid silently discarding
parts of the subtotal. Quantities and exchange-rate precision remain native.
For foreign-currency documents, base-currency fields retain their native precision.

**Supplier → Redondeo de impuestos en compras** can inherit the currency setting,
round each line's tax before adding, round the accumulated tax per tax row, or
use the native Accounts Settings choice. Item Tax Templates still determine rates
and exemptions. No tax rate is hardcoded. Commercial ties round away from zero,
including returns. Subtotal rounding can differ from line rounding by cents;
choose the supplier's convention.

An individual draft can select **Usar calculo original de redondeo** for an
exception. Disabling the currency policy restores the native calculation for
future draft calculations. It does not undo already submitted financial records.

Example: quantity 3 × rate 394.0678 produces an amount of 1182.20, tax 212.80 at
18%, and total 1395.00, with no artificial discount. The price stays 394.0678.
Existing Actual tax rows retain their type and manual amount as monetary inputs;
an active two-decimal policy normalizes their monetary precision to two decimals.
The same applies to manually entered document discounts. Historical adjustments
are not removed or scanned. Existing drafts adopt the policy when recalculated.

## Integration and isolation

- `before_validate` binds the calculator only on the current purchase document.
  Normal saves, imports, API inserts and submissions therefore calculate on the
  server before native validation, valuation, payment schedules and GL posting.
- All four purchase types share the same code: Supplier Quotation, Purchase
  Order, Purchase Receipt and Purchase Invoice. Existing controller classes and
  all other hooks remain installed, including e-invoicing and retention hooks.
- The native ERPNext calculator is reused. A scoped copy of the request-local
  settings selects the rounding method. Precision methods are temporarily bound
  to this document and its children. Shared metadata and module functions are
  never changed. `finally` restores settings, flags and methods after success or
  exceptions. Non-monetary input rounding retains the original system method.
- The native method identity is checked when a policy is active. If another app
  replaces the tax calculator, an explicit validation error requests review;
  disabling the policy keeps that app's original calculator available.
- The browser keeps existing currency/Actual-tax handlers. Preview calls are
  read-only, discard stale responses, and share server calculation. Save awaits
  the current preview. Only calculated read-only fields receive display precision;
  editable inputs keep native formatting to avoid exchange-rate callback conflicts.
- A hidden policy snapshot is written with each normally calculated document.
  Submitted-document previews use only the saved snapshot for display; they never
  recalculate historical amounts when currency settings change.

Direct SQL/db_set writes bypass Frappe validation and therefore this feature.
Standalone calls to `calculate_taxes_and_totals()` on unsaved documents without
`before_validate` also remain native unless they explicitly use
`igctools.purchase_rounding.engine.calculate(doc)`. Saving always applies the hook.
Mapped documents may show a native intermediate amount until form preview/save;
the mapping itself and the original source document are not modified.

## Release gate

1. Run the `Purchase rounding` CI workflow. The arithmetic harness executes the
   unchanged native v15.122.0 calculator and rounding functions, substituting only
   storage/master-data/HTML dependencies. Node tests cover preview races and
   preservation of the existing currency handler.
2. Run the bench integration suite on a disposable ERPNext v15.122.0 site:
   `bench --site test_site run-tests --module igctools.purchase_rounding.test_integration`.
   It tests saved PO → receipt → invoice, GL balance, quotations, Actual taxes,
   preview/save agreement, opt-out, configuration validation and historical snapshots.
   Never run this fixture-creating suite on production.
3. On a staging copy with the site's additional apps and scripts, test its own
   tax templates, withholding, purchase capture/import, invoice submission and
   e-invoicing path. Confirm DOP example amounts and unchanged USD purchases,
   especially manual Actual taxes when the exchange rate changes. CI does not
   include site-specific PowerPro/NubeF customizations.
4. Deploy the reviewed IGCTools commit and run normal site migration/build.
   Policies remain disabled. Enable the selected currency only after the staging
   checks pass. Refresh open forms to load the new asset.

For an arithmetic-only local check, set `IGC_UPSTREAM_SOURCE` to a directory with
the exact upstream files named `taxpy122.py` and `datapy122.py`, then run:

```sh
IGC_UPSTREAM_SOURCE=/path/to/upstream python -m unittest discover -s tests/purchase_rounding -p 'test_native_arithmetic.py' -v
node --test tests/purchase_rounding/test_client.js
```

Review and rerun these gates after upstream calculator changes. The module has
been written against Frappe/ERPNext 15.122.0; it is not a claim of compatibility
with every future version or every local tax extension.
