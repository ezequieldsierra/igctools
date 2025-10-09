# apps/igctools/igctools/overrides/job_card.py
from erpnext.manufacturing.doctype.job_card.job_card import JobCard as _JobCard
import frappe

class JobCard(_JobCard):
    def validate_job_card(self):
        """Override: NO exigir que total_completed_qty == for_quantity.

        Mantén validaciones básicas y evita la igualdad estricta del core.
        El on_submit del core seguirá llamando a ESTE método (override),
        por lo que no se disparará el throw por desigualdad.
        """
        # --- Validaciones mínimas seguras (ajústalas si quieres) ---
        fq = float(self.for_quantity or 0)
        tc = float(self.total_completed_qty or 0)

        if fq < 0 or tc < 0:
            frappe.throw("Las cantidades no pueden ser negativas.")

        # Si quieres permitir MENOR o MAYOR, no hagas nada más.
        # Si quieres permitir sólo >=, descomenta este bloque:
        # if tc < fq:
        #     frappe.throw("No puedes someter si lo completado es menor que el plan.")

        # IMPORTANTE:
        # No llamar a super().validate_job_card() porque ahí vive el check de igualdad estricto.
        # El resto de validaciones del ciclo de vida (on_submit) siguen funcionado normal.

    # No es necesario overridear on_submit si sólo te molestaba la igualdad.
    # El on_submit del padre seguirá corriendo y usará ESTE validate_job_card().
