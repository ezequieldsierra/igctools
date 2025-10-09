# apps/igctools/igctools/overrides/job_card.py

from erpnext.manufacturing.doctype.job_card.job_card import JobCard as _JobCard
import frappe

class JobCard(_JobCard):
    # 1) VALIDACIONES RELAJADAS (sin igualdad estricta)
    def validate_job_card(self):
        # (opcional) traza para confirmar que entra al override:
        # frappe.log_error("OVERRIDE validate_job_card HIT", "IGCTools Override")

        fq = float(self.for_quantity or 0)
        tc = float(self.total_completed_qty or 0)

        if fq < 0 or tc < 0:
            frappe.throw("Las cantidades no pueden ser negativas.")

        # Si quieres permitir solo >= (no menor al plan), descomenta:
        # if tc < fq:
        #     frappe.throw("No puedes someter si lo completado es menor que el plan.")

        # No llamar a super().validate_job_card(): ahí está el check que quitamos.

    # 2) DESACTIVAR bloqueos por secuencia previa (docstatus=1 del anterior)
    def validate_sequence_id(self):
        """No exigir que la operación previa esté cerrada/sometida para guardar/someter."""
        # (opcional) traza:
        # frappe.log_error("OVERRIDE validate_sequence_id HIT", "IGCTools Override")
        return

    # 3) Métodos defensivos por si tu core llama validaciones auxiliares:
    def validate_previous_operation_completed(self):
        return

    def validate_previous_job_cards_submitted(self):
        return
