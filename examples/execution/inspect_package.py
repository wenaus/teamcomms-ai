"""Read installed package provenance and return one bounded, deterministic result."""
import json,sys
from importlib.metadata import distribution
request=json.load(sys.stdin)
package=distribution('teamcomms-ai')
origin=json.loads(package.read_text('direct_url.json') or '{}')
revision=origin.get('vcs_info',{}).get('commit_id','local installation')
print(json.dumps({'outcome':'wrangle-ai executed the explicit package inspection offer successfully.',
 'evidence':['Installed TeamComms '+package.version+' at '+revision,
             'Accountable work '+request['work']['entry_id'],
             'Read-only package metadata inspection; no model invocation or production action.']}))
