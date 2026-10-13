from django import forms

from apps.vault.models import Material

from .models import Share, StudyGroup


class GroupForm(forms.ModelForm):
    class Meta:
        model = StudyGroup
        fields = ["name"]

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if not name:
            raise forms.ValidationError("Group name is required.")
        return name


class JoinGroupForm(forms.Form):
    join_code = forms.CharField(max_length=12, label="Join code")


class ShareForm(forms.Form):
    """Shape validation only. The authoritative checks (ownership, membership, risk)
    run again in services.save_share, so a crafted POST cannot skip them."""

    group = forms.ModelChoiceField(queryset=StudyGroup.objects.none())
    permission = forms.ChoiceField(choices=Share.Permission.choices, initial=Share.Permission.VIEW)
    allow_download = forms.BooleanField(required=False, label="Allow members to download the file")
    confirm_risk = forms.BooleanField(required=False, label="I understand and accept the risk")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        # Only groups the user belongs to can even be selected.
        self.fields["group"].queryset = StudyGroup.objects.filter(memberships__user=user)


class SharedMaterialEditForm(forms.ModelForm):
    """What an editor (non-owner with 'edit' permission) may change. No file, unit or tags."""

    class Meta:
        model = Material
        fields = ["title", "notes"]

    def clean_title(self):
        title = self.cleaned_data["title"].strip()
        if not title:
            raise forms.ValidationError("Title is required.")
        return title
