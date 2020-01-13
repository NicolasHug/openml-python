# License: BSD 3-Clause

from collections import OrderedDict
import re
import gzip
import io
import logging
import os
import pickle
import pyarrow.feather as feather

from typing import List, Optional, Union, Tuple, Iterable, Dict
import arff
import numpy as np
import pandas as pd
import scipy.sparse
from warnings import warn

from openml.base import OpenMLBase
from .data_feature import OpenMLDataFeature
from ..exceptions import PyOpenMLError


logger = logging.getLogger(__name__)


class OpenMLDataset(OpenMLBase):
    """Dataset object.

    Allows fetching and uploading datasets to OpenML.

    Parameters
    ----------
    name : str
        Name of the dataset.
    description : str
        Description of the dataset.
    format : str
        Format of the dataset which can be either 'arff' or 'sparse_arff'.
    cache_format : str, optional
        Format for caching the dataset which can be either 'feather' or 'pickle'.
    dataset_id : int, optional
        Id autogenerated by the server.
    version : int, optional
        Version of this dataset. '1' for original version.
        Auto-incremented by server.
    creator : str, optional
        The person who created the dataset.
    contributor : str, optional
        People who contributed to the current version of the dataset.
    collection_date : str, optional
        The date the data was originally collected, given by the uploader.
    upload_date : str, optional
        The date-time when the dataset was uploaded, generated by server.
    language : str, optional
        Language in which the data is represented.
        Starts with 1 upper case letter, rest lower case, e.g. 'English'.
    licence : str, optional
        License of the data.
    url : str, optional
        Valid URL, points to actual data file.
        The file can be on the OpenML server or another dataset repository.
    default_target_attribute : str, optional
        The default target attribute, if it exists.
        Can have multiple values, comma separated.
    row_id_attribute : str, optional
        The attribute that represents the row-id column,
        if present in the dataset.
    ignore_attribute : str | list, optional
        Attributes that should be excluded in modelling,
        such as identifiers and indexes.
    version_label : str, optional
        Version label provided by user.
        Can be a date, hash, or some other type of id.
    citation : str, optional
        Reference(s) that should be cited when building on this data.
    tag : str, optional
        Tags, describing the algorithms.
    visibility : str, optional
        Who can see the dataset.
        Typical values: 'Everyone','All my friends','Only me'.
        Can also be any of the user's circles.
    original_data_url : str, optional
        For derived data, the url to the original dataset.
    paper_url : str, optional
        Link to a paper describing the dataset.
    update_comment : str, optional
        An explanation for when the dataset is uploaded.
    status : str, optional
        Whether the dataset is active.
    md5_checksum : str, optional
        MD5 checksum to check if the dataset is downloaded without corruption.
    data_file : str, optional
        Path to where the dataset is located.
    features : dict, optional
        A dictionary of dataset features,
        which maps a feature index to a OpenMLDataFeature.
    qualities : dict, optional
        A dictionary of dataset qualities,
        which maps a quality name to a quality value.
    dataset: string, optional
        Serialized arff dataset string.
    """
    def __init__(self, name, description, format=None,
                 data_format='arff', cache_format='feather',
                 dataset_id=None, version=None,
                 creator=None, contributor=None, collection_date=None,
                 upload_date=None, language=None, licence=None,
                 url=None, default_target_attribute=None,
                 row_id_attribute=None, ignore_attribute=None,
                 version_label=None, citation=None, tag=None,
                 visibility=None, original_data_url=None,
                 paper_url=None, update_comment=None,
                 md5_checksum=None, data_file=None, features=None,
                 qualities=None, dataset=None):
        if dataset_id is None:
            if description and not re.match("^[\x00-\x7F]*$", description):
                # not basiclatin (XSD complains)
                raise ValueError("Invalid symbols in description: {}".format(
                    description))
            if citation and not re.match("^[\x00-\x7F]*$", citation):
                # not basiclatin (XSD complains)
                raise ValueError("Invalid symbols in citation: {}".format(
                    citation))
            if not re.match("^[a-zA-Z0-9_\\-\\.\\(\\),]+$", name):
                # regex given by server in error message
                raise ValueError("Invalid symbols in name: {}".format(name))
        # TODO add function to check if the name is casual_string128
        # Attributes received by querying the RESTful API
        self.dataset_id = int(dataset_id) if dataset_id is not None else None
        self.name = name
        self.version = int(version) if version is not None else None
        self.description = description
        self.cache_format = cache_format
        if format is None:
            self.format = data_format
        else:
            warn("The format parameter in the init will be deprecated "
                 "in the future."
                 "Please use data_format instead", DeprecationWarning)
            self.format = format
        self.creator = creator
        self.contributor = contributor
        self.collection_date = collection_date
        self.upload_date = upload_date
        self.language = language
        self.licence = licence
        self.url = url
        self.default_target_attribute = default_target_attribute
        self.row_id_attribute = row_id_attribute
        if isinstance(ignore_attribute, str):
            self.ignore_attribute = [ignore_attribute]
        elif isinstance(ignore_attribute, list) or ignore_attribute is None:
            self.ignore_attribute = ignore_attribute
        else:
            raise ValueError('Wrong data type for ignore_attribute. '
                             'Should be list.')
        self.version_label = version_label
        self.citation = citation
        self.tag = tag
        self.visibility = visibility
        self.original_data_url = original_data_url
        self.paper_url = paper_url
        self.update_comment = update_comment
        self.md5_checksum = md5_checksum
        self.data_file = data_file
        self.features = None
        self.qualities = None
        self._dataset = dataset

        if features is not None:
            self.features = {}
            for idx, xmlfeature in enumerate(features['oml:feature']):
                nr_missing = xmlfeature.get('oml:number_of_missing_values', 0)
                feature = OpenMLDataFeature(int(xmlfeature['oml:index']),
                                            xmlfeature['oml:name'],
                                            xmlfeature['oml:data_type'],
                                            xmlfeature.get('oml:nominal_value'),
                                            int(nr_missing))
                if idx != feature.index:
                    raise ValueError('Data features not provided '
                                     'in right order')
                self.features[feature.index] = feature

        self.qualities = _check_qualities(qualities)

        if data_file is not None:
            self.data_pickle_file, self.data_feather_file = self._create_pickle_in_cache(data_file)
        else:
            self.data_pickle_file = None
            self.data_feather_file = None

    @property
    def id(self) -> Optional[int]:
        return self.dataset_id

    def _get_repr_body_fields(self) -> List[Tuple[str, Union[str, int, List[str]]]]:
        """ Collect all information to display in the __repr__ body. """
        fields = {"Name": self.name,
                  "Version": self.version,
                  "Format": self.format,
                  "Licence": self.licence,
                  "Download URL": self.url,
                  "Data file": self.data_file,
                  "Pickle file": self.data_pickle_file,
                  "# of features": len(self.features)
                  if self.features is not None else None}
        if self.upload_date is not None:
            fields["Upload Date"] = self.upload_date.replace('T', ' ')
        if self.dataset_id is not None:
            fields["OpenML URL"] = self.openml_url
        if self.qualities is not None and self.qualities['NumberOfInstances'] is not None:
            fields["# of instances"] = int(self.qualities['NumberOfInstances'])

        # determines the order in which the information will be printed
        order = ["Name", "Version", "Format", "Upload Date", "Licence", "Download URL",
                 "OpenML URL", "Data File", "Pickle File", "# of features", "# of instances"]
        return [(key, fields[key]) for key in order if key in fields]

    def __eq__(self, other):

        if type(other) != OpenMLDataset:
            return False

        server_fields = {
            'dataset_id',
            'version',
            'upload_date',
            'url',
            'dataset',
            'data_file',
        }

        # check that the keys are identical
        self_keys = set(self.__dict__.keys()) - server_fields
        other_keys = set(other.__dict__.keys()) - server_fields
        if self_keys != other_keys:
            return False

        # check that values of the common keys are identical
        return all(self.__dict__[key] == other.__dict__[key]
                   for key in self_keys)

    def _download_data(self) -> None:
        """ Download ARFF data file to standard cache directory. Set `self.data_file`. """
        # import required here to avoid circular import.
        from .functions import _get_dataset_arff
        self.data_file = _get_dataset_arff(self)

    def _get_arff(self, format: str) -> Dict:
        """Read ARFF file and return decoded arff.

        Reads the file referenced in self.data_file.

        Parameters
        ----------
        format : str
            Format of the ARFF file.
            Must be one of 'arff' or 'sparse_arff' or a string that will be either of those
            when converted to lower case.



        Returns
        -------
        dict
            Decoded arff.

        """

        # TODO: add a partial read method which only returns the attribute
        # headers of the corresponding .arff file!
        import struct

        filename = self.data_file
        bits = (8 * struct.calcsize("P"))
        # Files can be considered too large on a 32-bit system,
        # if it exceeds 120mb (slightly more than covtype dataset size)
        # This number is somewhat arbitrary.
        if bits != 64 and os.path.getsize(filename) > 120000000:
            raise NotImplementedError("File {} too big for {}-bit system ({} bytes)."
                                      .format(filename, os.path.getsize(filename), bits))

        if format.lower() == 'arff':
            return_type = arff.DENSE
        elif format.lower() == 'sparse_arff':
            return_type = arff.COO
        else:
            raise ValueError('Unknown data format {}'.format(format))

        def decode_arff(fh):
            decoder = arff.ArffDecoder()
            return decoder.decode(fh, encode_nominal=True,
                                  return_type=return_type)

        if filename[-3:] == ".gz":
            with gzip.open(filename) as fh:
                return decode_arff(fh)
        else:
            with io.open(filename, encoding='utf8') as fh:
                return decode_arff(fh)

    def _parse_data_from_arff(
            self,
            arff_file_path: str
    ) -> Tuple[Union[pd.DataFrame, scipy.sparse.csr_matrix], List[bool], List[str]]:
        """ Parse all required data from arff file.

        Parameters
        ----------
        arff_file_path : str
            Path to the file on disk.

        Returns
        -------
        Tuple[Union[pd.DataFrame, scipy.sparse.csr_matrix], List[bool], List[str]]
            DataFrame or csr_matrix: dataset
            List[bool]: List indicating which columns contain categorical variables.
            List[str]: List of column names.
        """
        try:
            data = self._get_arff(self.format)
        except OSError as e:
            logger.critical("Please check that the data file {} is "
                            "there and can be read.".format(arff_file_path))
            raise e

        ARFF_DTYPES_TO_PD_DTYPE = {
            'INTEGER': 'integer',
            'REAL': 'floating',
            'NUMERIC': 'floating',
            'STRING': 'string'
        }
        attribute_dtype = {}
        attribute_names = []
        categories_names = {}
        categorical = []
        for i, (name, type_) in enumerate(data['attributes']):
            # if the feature is nominal and the a sparse matrix is
            # requested, the categories need to be numeric
            if (isinstance(type_, list)
                    and self.format.lower() == 'sparse_arff'):
                try:
                    # checks if the strings which should be the class labels
                    # can be encoded into integers
                    pd.factorize(type_)[0]
                except ValueError:
                    raise ValueError(
                        "Categorical data needs to be numeric when "
                        "using sparse ARFF."
                    )
            # string can only be supported with pandas DataFrame
            elif (type_ == 'STRING'
                  and self.format.lower() == 'sparse_arff'):
                raise ValueError(
                    "Dataset containing strings is not supported "
                    "with sparse ARFF."
                )

            # infer the dtype from the ARFF header
            if isinstance(type_, list):
                categorical.append(True)
                categories_names[name] = type_
                if len(type_) == 2:
                    type_norm = [cat.lower().capitalize()
                                 for cat in type_]
                    if set(['True', 'False']) == set(type_norm):
                        categories_names[name] = [
                            True if cat == 'True' else False
                            for cat in type_norm
                        ]
                        attribute_dtype[name] = 'boolean'
                    else:
                        attribute_dtype[name] = 'categorical'
                else:
                    attribute_dtype[name] = 'categorical'
            else:
                categorical.append(False)
                attribute_dtype[name] = ARFF_DTYPES_TO_PD_DTYPE[type_]
            attribute_names.append(name)

        if self.format.lower() == 'sparse_arff':
            X = data['data']
            X_shape = (max(X[1]) + 1, max(X[2]) + 1)
            X = scipy.sparse.coo_matrix(
                (X[0], (X[1], X[2])), shape=X_shape, dtype=np.float32)
            X = X.tocsr()
        elif self.format.lower() == 'arff':
            X = pd.DataFrame(data['data'], columns=attribute_names)

            col = []
            for column_name in X.columns:
                if attribute_dtype[column_name] in ('categorical',
                                                    'boolean'):
                    col.append(self._unpack_categories(
                        X[column_name], categories_names[column_name]))
                else:
                    col.append(X[column_name])
            X = pd.concat(col, axis=1)
        else:
            raise ValueError("Dataset format '{}' is not a valid format.".format(self.format))

        return X, categorical, attribute_names

    def _create_pickle_in_cache(self, data_file: str) -> Tuple[str, str]:
        """ Parse the arff and pickle the result. Update any old pickle objects. """
        data_pickle_file = data_file.replace('.arff', '.pkl.py3')
        data_feather_file = data_file.replace('.arff', '.feather')
        if self.cache_format == 'feather':
            if os.path.exists(data_feather_file):
                data = feather.read_feather(data_feather_file)
                with open(data_pickle_file, "rb") as fh:
                    try:
                        categorical, attribute_names = pickle.load(fh)
                    except EOFError:
                        # The file is likely corrupt, see #780.
                        # We deal with this when loading the data in `_load_data`.
                        return data_pickle_file, data_feather_file
        else:
            if os.path.exists(data_pickle_file):
                # Load the data to check if the pickle file is outdated (i.e. contains numpy array)
                with open(data_pickle_file, "rb") as fh:
                    try:
                        data, categorical, attribute_names = pickle.load(fh)
                    except EOFError:
                        # The file is likely corrupt, see #780.
                        # We deal with this when loading the data in `_load_data`.
                        return data_pickle_file, data_feather_file

                # Between v0.8 and v0.9 the format of pickled data changed from
                # np.ndarray to pd.DataFrame. This breaks some backwards compatibility,
                # e.g. for `run_model_on_task`. If a local file still exists with
                # np.ndarray data, we reprocess the data file to store a pickled
                # pd.DataFrame blob. See also #646.
                if isinstance(data, pd.DataFrame) or scipy.sparse.issparse(data):
                    logger.debug("Data pickle file already exists and is up to date.")
                    return data_pickle_file, data_feather_file

        # At this point either the pickle file does not exist, or it had outdated formatting.
        # We parse the data from arff again and populate the cache with a recent pickle file.
        X, categorical, attribute_names = self._parse_data_from_arff(data_file)

        if self.cache_format == "feather" and type(X) != scipy.sparse.csr.csr_matrix:
            logger.info("feather write")
            feather.write_feather(X, data_feather_file)
            with open(data_pickle_file, "wb") as fh:
                pickle.dump((categorical, attribute_names), fh, pickle.HIGHEST_PROTOCOL)
        else:
            logger.info("pickle write")
            self.cache_format = 'pickle'
            with open(data_pickle_file, "wb") as fh:
                pickle.dump((X, categorical, attribute_names), fh, pickle.HIGHEST_PROTOCOL)

        logger.debug("Saved dataset {did}: {name} to file {path}"
                     .format(did=int(self.dataset_id or -1),
                             name=self.name,
                             path=data_pickle_file)
                     )

        return data_pickle_file, data_feather_file

    def _load_data(self):
        """ Load data from pickle or arff. Download data first if not present on disk. """
        if self.data_pickle_file is None:
            if self.data_file is None:
                self._download_data()
            self.data_pickle_file, self.data_feather_file = self._create_pickle_in_cache(
                self.data_file)

        try:
            if self.cache_format == 'feather':
                logger.info("feather load data")
                data = feather.read_feather(self.data_feather_file)

                with open(self.data_pickle_file, "rb") as fh:
                    categorical, attribute_names = pickle.load(fh)
            else:
                logger.info("pickle load data")
                with open(self.data_pickle_file, "rb") as fh:
                    data, categorical, attribute_names = pickle.load(fh)
        except EOFError:
            logger.warning(
                "Detected a corrupt cache file loading dataset %d: '%s'. "
                "We will continue loading data from the arff-file, "
                "but this will be much slower for big datasets. "
                "Please manually delete the cache file if you want openml-python "
                "to attempt to reconstruct it."
                "" % (self.dataset_id, self.data_pickle_file)
            )
            data, categorical, attribute_names = self._parse_data_from_arff(self.data_file)
        except FileNotFoundError:
            raise ValueError("Cannot find a pickle file for dataset {} at "
                             "location {} ".format(self.name, self.data_pickle_file))

        return data, categorical, attribute_names

    @staticmethod
    def _convert_array_format(data, array_format, attribute_names):
        """Convert a dataset to a given array format.

        Converts to numpy array if data is non-sparse.
        Converts to a sparse dataframe if data is sparse.

        Parameters
        ----------
        array_format : str {'array', 'dataframe'}
            Desired data type of the output
            - If array_format='array'
                If data is non-sparse
                    Converts to numpy-array
                    Enforces numeric encoding of categorical columns
                    Missing values are represented as NaN in the numpy-array
                else returns data as is
            - If array_format='dataframe'
                If data is sparse
                    Works only on sparse data
                    Converts sparse data to sparse dataframe
                else returns data as is

        """

        if array_format == "array" and not scipy.sparse.issparse(data):
            # We encode the categories such that they are integer to be able
            # to make a conversion to numeric for backward compatibility
            def _encode_if_category(column):
                if column.dtype.name == 'category':
                    column = column.cat.codes.astype(np.float32)
                    mask_nan = column == -1
                    column[mask_nan] = np.nan
                return column
            if data.ndim == 2:
                columns = {
                    column_name: _encode_if_category(data.loc[:, column_name])
                    for column_name in data.columns
                }
                data = pd.DataFrame(columns)
            else:
                data = _encode_if_category(data)
            try:
                return np.asarray(data, dtype=np.float32)
            except ValueError:
                raise PyOpenMLError(
                    'PyOpenML cannot handle string when returning numpy'
                    ' arrays. Use dataset_format="dataframe".'
                )
        elif array_format == "dataframe":
            if scipy.sparse.issparse(data):
                return pd.SparseDataFrame(data, columns=attribute_names)
            else:
                return data
        else:
            data_type = "sparse-data" if scipy.sparse.issparse(data) else "non-sparse data"
            logger.warning(
                "Cannot convert %s (%s) to '%s'. Returning input data."
                % (data_type, type(data), array_format)
            )
        return data

    @staticmethod
    def _unpack_categories(series, categories):
        col = []
        for x in series:
            try:
                col.append(categories[int(x)])
            except (TypeError, ValueError):
                col.append(np.nan)
        # We require two lines to create a series of categories as detailed here:
        # https://pandas.pydata.org/pandas-docs/version/0.24/user_guide/categorical.html#series-creation  # noqa E501
        raw_cat = pd.Categorical(col, ordered=True, categories=categories)
        return pd.Series(raw_cat, index=series.index, name=series.name)

    def get_data(
            self,
            target: Optional[Union[List[str], str]] = None,
            include_row_id: bool = False,
            include_ignore_attribute: bool = False,
            dataset_format: str = "dataframe",
    ) -> Tuple[
            Union[np.ndarray, pd.DataFrame, scipy.sparse.csr_matrix],
            Optional[Union[np.ndarray, pd.DataFrame]],
            List[bool],
            List[str]
    ]:
        """ Returns dataset content as dataframes or sparse matrices.

        Parameters
        ----------
        target : string, List[str] or None (default=None)
            Name of target column to separate from the data.
            Splitting multiple columns is currently not supported.
        include_row_id : boolean (default=False)
            Whether to include row ids in the returned dataset.
        include_ignore_attribute : boolean (default=False)
            Whether to include columns that are marked as "ignore"
            on the server in the dataset.
        dataset_format : string (default='dataframe')
            The format of returned dataset.
            If ``array``, the returned dataset will be a NumPy array or a SciPy sparse matrix.
            If ``dataframe``, the returned dataset will be a Pandas DataFrame or SparseDataFrame.

        Returns
        -------
        X : ndarray, dataframe, or sparse matrix, shape (n_samples, n_columns)
            Dataset
        y : ndarray or pd.Series, shape (n_samples, ) or None
            Target column
        categorical_indicator : boolean ndarray
            Mask that indicate categorical features.
        attribute_names : List[str]
            List of attribute names.
        """
        data, categorical, attribute_names = self._load_data()

        to_exclude = []
        if not include_row_id and self.row_id_attribute is not None:
            if isinstance(self.row_id_attribute, str):
                to_exclude.append(self.row_id_attribute)
            elif isinstance(self.row_id_attribute, Iterable):
                to_exclude.extend(self.row_id_attribute)

        if not include_ignore_attribute and self.ignore_attribute is not None:
            if isinstance(self.ignore_attribute, str):
                to_exclude.append(self.ignore_attribute)
            elif isinstance(self.ignore_attribute, Iterable):
                to_exclude.extend(self.ignore_attribute)

        if len(to_exclude) > 0:
            logger.info("Going to remove the following attributes:"
                        " %s" % to_exclude)
            keep = np.array([True if column not in to_exclude else False
                             for column in attribute_names])
            if hasattr(data, 'iloc'):
                data = data.iloc[:, keep]
            else:
                data = data[:, keep]
            categorical = [cat for cat, k in zip(categorical, keep) if k]
            attribute_names = [att for att, k in
                               zip(attribute_names, keep) if k]

        if target is None:
            data = self._convert_array_format(data, dataset_format,
                                              attribute_names)
            targets = None
        else:
            if isinstance(target, str):
                if ',' in target:
                    target = target.split(',')
                else:
                    target = [target]
            targets = np.array([True if column in target else False
                                for column in attribute_names])
            if np.sum(targets) > 1:
                raise NotImplementedError(
                    "Number of requested targets %d is not implemented." %
                    np.sum(targets)
                )
            target_categorical = [
                cat for cat, column in zip(categorical, attribute_names)
                if column in target
            ]
            target_dtype = int if target_categorical[0] else float

            if hasattr(data, 'iloc'):
                x = data.iloc[:, ~targets]
                y = data.iloc[:, targets]
            else:
                x = data[:, ~targets]
                y = data[:, targets].astype(target_dtype)

            categorical = [cat for cat, t in zip(categorical, targets)
                           if not t]
            attribute_names = [att for att, k in zip(attribute_names, targets)
                               if not k]

            x = self._convert_array_format(x, dataset_format, attribute_names)
            if scipy.sparse.issparse(y):
                y = np.asarray(y.todense()).astype(target_dtype).flatten()
            y = y.squeeze()
            y = self._convert_array_format(y, dataset_format, attribute_names)
            y = y.astype(target_dtype) if dataset_format == 'array' else y
            data, targets = x, y

        return data, targets, categorical, attribute_names

    def retrieve_class_labels(self, target_name: str = 'class') -> Union[None, List[str]]:
        """Reads the datasets arff to determine the class-labels.

        If the task has no class labels (for example a regression problem)
        it returns None. Necessary because the data returned by get_data
        only contains the indices of the classes, while OpenML needs the real
        classname when uploading the results of a run.

        Parameters
        ----------
        target_name : str
            Name of the target attribute

        Returns
        -------
        list
        """
        for feature in self.features.values():
            if (feature.name == target_name) and (feature.data_type == 'nominal'):
                return feature.nominal_values
        return None

    def get_features_by_type(self, data_type, exclude=None,
                             exclude_ignore_attribute=True,
                             exclude_row_id_attribute=True):
        """
        Return indices of features of a given type, e.g. all nominal features.
        Optional parameters to exclude various features by index or ontology.

        Parameters
        ----------
        data_type : str
            The data type to return (e.g., nominal, numeric, date, string)
        exclude : list(int)
            Indices to exclude (and adapt the return values as if these indices
                        are not present)
        exclude_ignore_attribute : bool
            Whether to exclude the defined ignore attributes (and adapt the
            return values as if these indices are not present)
        exclude_row_id_attribute : bool
            Whether to exclude the defined row id attributes (and adapt the
            return values as if these indices are not present)

        Returns
        -------
        result : list
            a list of indices that have the specified data type
        """
        if data_type not in OpenMLDataFeature.LEGAL_DATA_TYPES:
            raise TypeError("Illegal feature type requested")
        if self.ignore_attribute is not None:
            if not isinstance(self.ignore_attribute, list):
                raise TypeError("ignore_attribute should be a list")
        if self.row_id_attribute is not None:
            if not isinstance(self.row_id_attribute, str):
                raise TypeError("row id attribute should be a str")
        if exclude is not None:
            if not isinstance(exclude, list):
                raise TypeError("Exclude should be a list")
            # assert all(isinstance(elem, str) for elem in exclude),
            #            "Exclude should be a list of strings"
        to_exclude = []
        if exclude is not None:
            to_exclude.extend(exclude)
        if exclude_ignore_attribute and self.ignore_attribute is not None:
            to_exclude.extend(self.ignore_attribute)
        if exclude_row_id_attribute and self.row_id_attribute is not None:
            to_exclude.append(self.row_id_attribute)

        result = []
        offset = 0
        # this function assumes that everything in to_exclude will
        # be 'excluded' from the dataset (hence the offset)
        for idx in self.features:
            name = self.features[idx].name
            if name in to_exclude:
                offset += 1
            else:
                if self.features[idx].data_type == data_type:
                    result.append(idx - offset)
        return result

    def _get_file_elements(self) -> Dict:
        """ Adds the 'dataset' to file elements. """
        file_elements = {}
        path = None if self.data_file is None else os.path.abspath(self.data_file)

        if self._dataset is not None:
            file_elements['dataset'] = self._dataset
        elif path is not None and os.path.exists(path):
            with open(path, 'rb') as fp:
                file_elements['dataset'] = fp.read()
            try:
                dataset_utf8 = str(file_elements['dataset'], 'utf8')
                arff.ArffDecoder().decode(dataset_utf8, encode_nominal=True)
            except arff.ArffException:
                raise ValueError("The file you have provided is not a valid arff file.")
        elif self.url is None:
            raise ValueError("No valid url/path to the data file was given.")
        return file_elements

    def _parse_publish_response(self, xml_response: Dict):
        """ Parse the id from the xml_response and assign it to self. """
        self.dataset_id = int(xml_response['oml:upload_data_set']['oml:id'])

    def _to_dict(self) -> 'OrderedDict[str, OrderedDict]':
        """ Creates a dictionary representation of self. """
        props = ['id', 'name', 'version', 'description', 'format', 'creator',
                 'contributor', 'collection_date', 'upload_date', 'language',
                 'licence', 'url', 'default_target_attribute',
                 'row_id_attribute', 'ignore_attribute', 'version_label',
                 'citation', 'tag', 'visibility', 'original_data_url',
                 'paper_url', 'update_comment', 'md5_checksum']

        data_container = OrderedDict()  # type: 'OrderedDict[str, OrderedDict]'
        data_dict = OrderedDict([('@xmlns:oml', 'http://openml.org/openml')])
        data_container['oml:data_set_description'] = data_dict

        for prop in props:
            content = getattr(self, prop, None)
            if content is not None:
                data_dict["oml:" + prop] = content

        return data_container


def _check_qualities(qualities):
    if qualities is not None:
        qualities_ = {}
        for xmlquality in qualities:
            name = xmlquality['oml:name']
            if xmlquality.get('oml:value', None) is None:
                value = float('NaN')
            elif xmlquality['oml:value'] == 'null':
                value = float('NaN')
            else:
                value = float(xmlquality['oml:value'])
            qualities_[name] = value
        return qualities_
    else:
        return None
