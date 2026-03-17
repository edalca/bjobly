from frappe.core.doctype.user.user import User


class BjoblyUser(User):
    def validate_email_type(self, email):
        pass

    def _validate_data_fields(self):
        # Skip email format validation to allow non-email usernames (e.g. "mgarcia")
        meta_backup = []
        for df in self.meta.get_data_fields():
            if df.get("options") == "Email":
                meta_backup.append((df, df.options))
                df.options = None
        try:
            super()._validate_data_fields()
        finally:
            for df, original_options in meta_backup:
                df.options = original_options
