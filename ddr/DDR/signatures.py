"""
signatures - functions for choosing signatures of entity and collection objects

Entity and Collection objects have a "signature_id" field.

EXAMPLE

collection_path = '/var/www/media/ddr/ddr-testing-333'
basepath = '/var/www/media/ddr'
from DDR import signatures
from DDR import util
paths = util.find_meta_files(collection_path, recursive=True, files_first=True, force_read=True)
identifiers = signatures.signatures(paths, basepath)

"""

from datetime import datetime
import json
import logging
logger = logging.getLogger(__name__)
import os

from DDR import config
from DDR import identifier
from DDR import models
from DDR import util


JSON_FIELDS = {
    'sort': 1,
    'signature_id': '',
}

# TODO derive this from repo_models/identifiers.py!
ROLE_NUMBERS = {
    'mezzanine': 0,
    'master': 1,
    'transcript': 2,
}
    
# TODO derive this from repo_models/identifiers.py!
MODELS_DOWN = [
    'collection',
    'entity',
    'segment',
    'file',
]

class SigIdentifier(identifier.Identifier):
    """Subclass of Identifier used for finding/assigning object signature files
    """
    sort = 999999
    signature = None    # immediate signature, next in chain
    signature_id = None # ID of ultimate signature (should be a file)
    sort_key = None
    
    def __repr__(self):
        return '<%s.%s %s:%s sort=%s,sid=%s>' % (
            self.__module__, self.__class__.__name__,
            self.model, self.id,
            self.sort, self.signature_id
        )
    
    def __lt__(self, other):
        """Enables Pythonic sorting"""
        return self.sort_key < other.sort_key
    
    def __init__(self, *args, **kwargs):
        # Load Identifier, read .json and add sort/signature to self
        # These are used for sorting
        super(SigIdentifier, self).__init__(*args, **kwargs)
        
        # read from .json file
        data = self._read_fields(self.path_abs('json'))
        for key,val in data.iteritems():
            if val:
                setattr(self, key, val)
        
        # prep sorting key
        sort_key = self.parts
        if self.model == 'file':
            # insert file sort before sha1
            sort_key['role'] = ROLE_NUMBERS[self.parts['role']]
            sha1 = sort_key.pop('sha1')
            sort_key['sort'] = self.sort
            sort_key['sha1'] = sha1
        elif self.model == 'entity':
            # insert entity sort before eid
            eid = sort_key.pop('eid')
            sort_key['sort'] = self.sort
            sort_key['eid'] = eid
            
        self.sort_key = sort_key.values()
    
    def _read_fields(self, path):
        # extracts only specified fields from JSON
        data = {}
        with open(path, 'r') as f:
            for d in json.loads(f.read()):
                key = d.keys()[0]
                if key in JSON_FIELDS.keys():
                    # coerces to int
                    if d.get(key) and isinstance(JSON_FIELDS[key], int):
                        data[key] = int(d[key])
                    else:
                        data[key] = d[key]
        return data
    
    def _signature_id(self):
        """Follow chain of object signatures to end, return last object.id
        """
        if not self.signature:
            return self.id
        return self.signature._signature_id()


def _metadata_paths(collection_path):
    return util.find_meta_files(collection_path, recursive=True, files_first=True, force_read=True)

def _load_identifiers(paths, basepath):
    """Loads and sorts list
    """
    logging.debug('Loading identifiers')
    identifiers_unsorted = {}
    logging.debug('%s paths [%s,...]' % (len(paths), paths[0]))
    for path in paths:
        if util.path_matches_model(path, 'file'):
            # this works on file paths but not on entities/collections
            basename_noext = os.path.splitext(os.path.basename(path))[0]
            i = SigIdentifier(basename_noext, base_path=basepath)
        else:
            # entities, segments, collections
            oid = os.path.basename(os.path.dirname(path))
            i = SigIdentifier(oid, base_path=basepath)
        if not identifiers_unsorted.get(i.model):
            identifiers_unsorted[i.model] = []
        identifiers_unsorted[i.model].append(i)
        
    # - SORT THE LISTS
    logging.debug('sorting')
    identifiers = {}
    for model,ids in identifiers_unsorted.iteritems():
        identifiers[model] = sorted(ids)
    return identifiers

def _models_parent_child():
    """List pairs of parent-child models, bottom-up
    
    @returns: list [(parent,child), ...]
    """
    repo_models = identifier.MODEL_REPO_MODELS.keys()
    pairs = []
    for i in identifier.IDENTIFIERS:
        pmodel = i['model']
        if pmodel in repo_models:
            for cmodel in i['children']:
                pairs.append((pmodel,cmodel))
    pairs.reverse()
    return pairs

def _choose_signatures(identifiers):
    # - FOR EACH NON-FILE,
    # - ENUMERATE FILES LIST FROM LAST (FIRST TIME, THIS IS 0)
    # - IF FILE IS CHILD OF ANCESTOR, SEE IF PUBLISHABLE
    # - IF CHILD OF ANCESTOR AND PUBLISHABLE, ASSIGN FILE_ID TO SIG ID, SET LAST TO n
    logging.debug('Choosing signatures')
    model_pairs = _models_parent_child()
    for parent_model,child_model in model_pairs:
        logging.debug('%s (%s children)' % (parent_model, len(identifiers.get(parent_model, []))))
        last = 0
        for pi in identifiers.get(parent_model, []):
            ## loop through list of children, starting with the last
            #for n,ci in enumerate(identifiers.get(child_model, [])[last:]):
            for n,ci in enumerate(identifiers.get(child_model, [])):
                if pi.id in ci.id:
                    pi.signature = ci
                    last = n + last
                    break
    # 
    # At this point, collection.signature and possibly some entity.signature,
    # will not be Files.
    # Go back through and replace these with files
    logging.debug('Replacing signature objects with ids')
    parent_models = identifier.CHILDREN.keys()
    # don't waste time looping on files
    for model in parent_models:
        for i in identifiers.get(model, []):
            i.signature_id = i._signature_id()
    logging.debug('Done')
    return identifiers

def signatures(paths, base_path):
    """Chooses signatures for objects in list of paths
    
    @param paths: list
    @base_path: str
    @returns: list of SigIdentifier objects
    """
    return _choose_signatures(_load_identifiers(paths, base_path))

def assign_signatures(collection):
    """Read collection .json files, assign signatures, write files
    """
    start = datetime.now()
    logging.debug('Collecting identifiers')
    identifiers = signatures(
        util.find_meta_files(
            collection.identifier.path_abs(),
            recursive=True, files_first=True, force_read=True
        ),
        collection.identifier.basepath
    )
    logging.debug('Writing changes')
    updated = []
    for model,oidentifiers in identifiers.iteritems():
        for n,oi in enumerate(oidentifiers):
            logging.debug('%s/%s %s' % (n+1, len(oidentifiers), oi.id))
            o = oi.object()
            if o.signature_id != oi.signature_id:
                updated.append(o.id)
            o.signature_id = oi.signature_id
            o.write_json()
    finish = datetime.now()
    elapsed = finish - start
    logging.debug('DONE (%s elapsed)' % elapsed)
    logging.debug('NOTE: METADATA FILES ARE NOT COMMITTED!')
    return updated

def _print_identifiers(identifiers, models=MODELS_DOWN):
    for model in models:
        if model in identifiers.keys():
            for oid in identifiers[model]:
                print oid
