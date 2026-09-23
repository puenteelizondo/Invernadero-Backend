import django_filters

from .models import Reading


class ReadingFilter(django_filters.FilterSet):
    date_from = django_filters.IsoDateTimeFilter(field_name="timestamp", lookup_expr="gte")
    date_to = django_filters.IsoDateTimeFilter(field_name="timestamp", lookup_expr="lte")

    class Meta:
        model = Reading
        fields = ["sensor", "date_from", "date_to"]