from typing import Tuple

from emannotationschemas import get_schema
from emannotationschemas import models as em_models
from emannotationschemas.flatten import flatten_dict
from emannotationschemas.schemas.base import ReferenceAnnotation
from marshmallow import EXCLUDE, Schema

from .errors import SelfReferenceTableError, TableNameNotFound


class DynamicSchemaClient:
    def __init__(self) -> None:
        pass

    def get_schema(self, schema_type: str):
        return get_schema(schema_type)

    def get_flattened_schema(self, schema_type: str):
        Schema = get_schema(schema_type)
        return em_models.create_flattened_schema(Schema)

    def create_annotation_model(
        self,
        table_name: str,
        schema_type: str,
        table_metadata: dict = None,
        with_crud_columns: bool = True,
    ):
        return em_models.make_annotation_model(
            table_name,
            schema_type,
            table_metadata=table_metadata,
            with_crud_columns=with_crud_columns,
        )

    def create_segmentation_model(
        self,
        table_name: str,
        schema_type: str,
        segmentation_source: str,
        table_metadata: dict = None,
        with_crud_columns: bool = True,
    ):
        return em_models.make_segmentation_model(
            table_name,
            schema_type,
            segmentation_source,
            table_metadata,
            with_crud_columns,
        )

    def create_reference_annotation_model(
        self,
        table_name: str,
        schema_type: str,
        target_table: str,
        segmentation_source: str = None,
        table_metadata: dict = None,
        with_crud_columns: bool = True,
    ):
        return em_models.make_reference_annotation_model(
            table_name,
            schema_type,
            target_table,
            segmentation_source,
            table_metadata,
            with_crud_columns,
        )

    def flattened_schema_data(self, data):
        return flatten_dict(data)

    def _split_flattened_schema(self, schema_type: str):
        schema_type = get_schema(schema_type)

        (
            flat_annotation_schema,
            flat_segmentation_schema,
        ) = em_models.split_annotation_schema(schema_type)

        return flat_annotation_schema, flat_segmentation_schema

    def _split_flattened_schema_data(
        self, schema_type: str, data: dict
    ) -> Tuple[dict, dict]:
        schema_type = get_schema(schema_type)
        schema = schema_type(context={"postgis": True})
        data = schema.load(data, unknown=EXCLUDE)

        check_is_nested = any(isinstance(i, dict) for i in data.values())
        if check_is_nested:
            data = flatten_dict(data)

        (
            flat_annotation_schema,
            flat_segmentation_schema,
        ) = em_models.split_annotation_schema(schema_type)

        return (
            self._map_values_to_schema(data, flat_annotation_schema),
            self._map_values_to_schema(data, flat_segmentation_schema),
        )

    def _map_values_to_schema(self, data: dict, schema: Schema):
        return {
            key: data[key]
            for key, value in schema._declared_fields.items()
            if key in data
        }

    def _parse_schema_metadata_params(
        self,
        schema_type: str,
        table_name: str,
        table_metadata: dict,
        existing_tables: list,
    ):
        reference_table = None
        track_updates = None

        for param, value in table_metadata.items():
            if param == "reference_table":
                Schema = self.get_schema(schema_type)
                if not issubclass(Schema, ReferenceAnnotation):
                    raise TypeError(
                        "Reference table must be a ReferenceAnnotation schema type"
                    )
                if table_name == value:
                    raise SelfReferenceTableError(
                        f"{reference_table} must target a different table not {table_name}"
                    )
                if value not in existing_tables:
                    raise TableNameNotFound(
                        f"Reference table target: '{value}' does not exist"
                    )
                reference_table = value
            elif param == "track_target_id_updates":
                track_updates = value
        return reference_table, track_updates
