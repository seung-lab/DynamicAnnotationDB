from dynamicannotationdb.interface import DynamicAnnotationInterface
from dynamicannotationdb.errors import AnnotationInsertLimitExceeded
from dynamicannotationdb.models import Metadata as AnnoMetadata
from dynamicannotationdb.key_utils import get_table_name_from_table_id, build_table_id
from emannotationschemas import get_flat_schema
from marshmallow import INCLUDE
from sqlalchemy.exc import ArgumentError, InvalidRequestError, OperationalError, IntegrityError
from sqlalchemy.orm.exc import NoResultFound

from typing import List
import datetime
import logging
import json


class DynamicAnnotationClient(DynamicAnnotationInterface):
    def __init__(self, aligned_volume, sql_base_uri):
        super().__init__(sql_base_uri)

        self.aligned_volume = aligned_volume

        self._table = None
        self._cached_schemas = {}

    @property
    def table(self):
        return self._table

    def load_table(self, table_name: str):
        """Load a table

        Parameters
        ----------
        table_name : str
            name of table

        Returns
        -------
        DeclarativeMeta
            the sqlalchemy table of that name
        """
        self._table = self.cached_table(table_name)
        return self._table

    def get_existing_table_names(self) -> List[str]:
        """Get all annotation tables that exist for a aligned_volume

        Returns
        -------
        List[str]
            list of table names that exist
        """
        table_ids = self.get_existing_tables()
        table_names = [get_table_name_from_table_id(tid) for tid in table_ids]
        return table_names

    def get_existing_tables_metadata(self) -> List[dict]:
        """Get all the metadata for all tables

        Returns
        -------
        List[dict]
            all table metadata that exist
        """
        return [
            self.get_table_metadata(self.aligned_volume, table_name)
            for table_name in self.get_existing_tables()
        ]

    def create_table(self, table_name: str,
                     schema_type: str,
                     metadata_dict: dict):
        """Create a new annotation table

        Parameters
        ----------
        table_name : str
            name of new table
            
        schema_type : str
            type of schema for that table

        metadata_dict : dict
             metadata to attach ::
             
        dict: {
            "description": "a string with a human readable explanation of \
                            what is in the table. Including who made it"
            "user_id": "user_id"
            "reference_table": "reference table name, if required by this schema"
            }

        Returns
        -------
        [type]
            [description]
        """
        # TODO: check that schemas that are reference schemas
        # have a reference_table in their metadata
        return self.create_annotation_table(self.aligned_volume,
                                            table_name,
                                            schema_type,
                                            metadata_dict)

    def delete_table(self, table_name: str) -> bool:
        """Marks a table for deletion, which will
        remove it from user visible calls
        and stop materialization from happening on this table
        only updates metadata to reflect deleted timestamp.

        Parameters
        ----------
        table_name : str
             name of table to mark for deletion

        Returns
        -------
        bool
            whether table was successfully deleted
        """
        table_id = build_table_id(self.aligned_volume, table_name)
        metadata = self.cached_session.query(AnnoMetadata). \
            filter(AnnoMetadata.table_name == table_id).first()
        metadata.deleted = datetime.datetime.now()
        self.cached_session.update(metadata)
        self.commit_session()
        return True

    def drop_table(self, table_name: str) -> bool:
        """Drop a table, actually removes it from the database
        along with segmentation tables associated with it

        Parameters
        ----------
        table_name : str
            name of table to drop

        Returns
        -------
        bool
            whether drop was successful
        """
        return self._drop_table(self.aligned_volume, table_name)

    def insert_annotations(self, table_name: str,
                          annotations: List[dict]):
        """Insert some annotations.

        Parameters
        ----------
        table_name : str
            name of target table to insert annotations
        annotations : list of dict
            a list of dicts with the annotations
                                that meet the schema

        Returns
        -------
        [type]
            [description]

        Raises
        ------
        AnnotationInsertLimitExceeded
            Exception raised when amount of annotations exceeds defined limit.
        """
        insertion_limit = 10_000

        if len(annotations) > insertion_limit:
            raise AnnotationInsertLimitExceeded(len(annotations), insertion_limit)

        schema_type = self.get_table_schema(self.aligned_volume, table_name)

        table_id = build_table_id(self.aligned_volume, table_name)

        AnnotationModel = self.cached_table(table_id)

        formatted_anno_data = []
        for annotation in annotations:

            annotation_data, __ = self._get_flattened_schema_data(schema_type, annotation)
            if annotation.get('id'):
                annotation_data['id'] = annotation['id']

            annotation_data['created'] = datetime.datetime.now()
            annotation_data['valid'] = True
            formatted_anno_data.append(annotation_data)

        annos = [AnnotationModel(**annotation_data) for annotation_data in formatted_anno_data]

        try:
            self.cached_session.add_all(annos)
            self.commit_session()
        except InvalidRequestError as e:
            self.cached_session.rollback()
            logging.error(f"Data commit error: {e}")
            return False
        return True

    def get_annotations(self, table_name: str,
                       annotation_ids: List[int]) -> List[dict]:
        """Get a set of annotations by ID

        Parameters
        ----------
        table_name : str
            name of table
        annotation_ids : List[int]
            list of annotation ids to get

        Returns
        -------
        List[dict]
            list of returned annotations
        """
        schema_type = self.get_table_schema(self.aligned_volume, table_name)

        table_id = build_table_id(self.aligned_volume, table_name)

        AnnotationModel = self.cached_table(table_id)

        annotations = self.cached_session.query(AnnotationModel). \
            filter(AnnotationModel.id.in_([x for x in annotation_ids])).all()

        try:
            FlatSchema = get_flat_schema(schema_type)
            schema = FlatSchema(unknown=INCLUDE)
            data = []

            for anno in annotations:
                anno_data = anno.__dict__
                anno_data['created'] = str(anno_data.get('created'))
                anno_data['deleted'] = str(anno_data.get('deleted'))
                anno_data.pop('_sa_instance_state', None)
                merged_data = {**anno_data}
                data.append(merged_data)

            return schema.load(data, many=True)

        except Exception as e:
            logging.exception(e)
            return f"No entries found for {annotation_ids}"

    def update_annotation(self, table_name: str,
                          annotation: dict):
        """Update an annotation

        Parameters
        ----------
        table_name : str
            name of targeted table to update annotations
        anno_id : int
            ID of annotation to update
        annotation : dict
            new data for that annotation

        Returns
        -------
        [type]
            [description]

        Raises
        ------
        """
        anno_id = annotation.get('id')
        if not anno_id:
            return "Annotation requires an 'id' to update targeted row"
        schema_type = self.get_table_schema(self.aligned_volume, table_name)

        table_id = build_table_id(self.aligned_volume, table_name)

        AnnotationModel = self.cached_table(table_id)

        new_annotation, __ = self._get_flattened_schema_data(schema_type, annotation)

        new_annotation['created'] = datetime.datetime.now()
        new_annotation['valid'] = True

        new_data = AnnotationModel(**new_annotation)
        try:
            old_anno = self.cached_session.query(AnnotationModel).filter(AnnotationModel.id == anno_id).one()

            if old_anno.superceded_id:
                return f"Annotation with id {anno_id} has already been superseded by {old_anno.superceded_id},\
                        update {old_anno.superceded_id} instead"

            self.cached_session.add(new_data)
            self.cached_session.flush()

            old_anno.superceded_id = new_data.id

            old_anno.valid = False

            self.commit_session()

            return f"id {anno_id} updated"
        except NoResultFound:
            return f"No result found for {anno_id}"

    def delete_annotation(self, table_name: str,
                          annotation_ids: List[int]):
        """Delete annotations by ids

        Parameters
        ----------
        table_name : str
            name of table to delete from
        annotation_ids : List[int]
            list of ids to delete

        Returns
        -------

        Raises
        ------
        """
        table_id = build_table_id(self.aligned_volume, table_name)
        Model = self.cached_table(table_id)

        annotations = self.cached_session.query(Model).filter(Model.id.in_(annotation_ids)).all()
        if annotations:
            deleted_time = datetime.datetime.now()
            for annotation in annotations:
                annotation.deleted = deleted_time
            self.commit_session()
        else:
            return None
        return True
