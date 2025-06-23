#!/usr/bin/env python3
"""
OJA – OJS automated file submission
combines REST API (file uploads) and Web API (galley creation) for automated file submission

features:
- automated galley creation
- automated file upload
- automated page number extraction from PDF and publication update
- automated conflict detection and resolution

USAGE:
    oja <submission_id_or_path>
    oja 8661
    oja 12-23_8661_author/
    oja ./submissions/8661_data/
    oja 8661 --settings # reconfigure OJS settings
    oja 8661 --dry-run  # preview submission without uploading
    oja 8661 --debug    # enable debug output to troubleshoot dependent files
    oja 8661 --skip     # skip all confirmation prompts

Questions? Bugs? → GitHub
"""

import sys
import argparse
import requests
import getpass
from pathlib import Path
from bs4 import BeautifulSoup
import time
import re
import fitz     # PyMuPDF
import zipfile
import tempfile
import shutil

def natural_sort_key(filename):
    """Create a key for natural sorting of filenames with numbers"""
    # Split filename into parts, converting number sequences to integers
    parts = re.split(r'(\d+)', str(filename))
    result = []
    for part in parts:
        if part.isdigit():
            result.append(int(part))
        else:
            result.append(part.lower())
    return result

class Colors:
    RED = '\033[91m'      # Failures/errors
    GREEN = '\033[92m'    # Success
    YELLOW = '\033[93m'   # Warnings/issues
    BLUE = '\033[94m'     # Progress/info
    PURPLE = '\033[95m'   # Prompts
    CYAN = '\033[96m'     # Headers
    GRAY = '\033[90m'     # Subdued
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    ITALIC = '\033[3m'
    RESET = '\033[0m'

class OJSConfig:
    """Handles OJS configuration and credentials"""
    
    def __init__(self):
        self.env_file = Path('.env')
        self.config = {}
        self.load_config()
    
    def load_config(self):
        """Load configuration from .env file"""
        if self.env_file.exists():
            with open(self.env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        self.config[key] = value
    
    def save_config(self):
        """Save configuration to .env file"""
        with open(self.env_file, 'w') as f:
            f.write("# OJS Automation Configuration\n")
            for key, value in self.config.items():
                f.write(f"{key}={value}\n")
    
    def get_or_prompt_config(self, force_settings=False):
        """Get configuration or prompt user for missing values"""
        required_keys = {
            'OJS_BASE_URL': 'OJS Base URL',
            'OJS_API_TOKEN': 'OJS API Key (from your user profile)',
            'OJS_USERNAME': 'OJS Username',
            'OJS_PASSWORD': 'OJS Password'
        }
        
        needs_config = force_settings or not all(key in self.config for key in required_keys)
        
        if needs_config:
            print(f"{Colors.CYAN}{Colors.UNDERLINE}OJS Configuration Setup{Colors.RESET}")
            
            for key, description in required_keys.items():
                current_value = self.config.get(key, '')
                
                if key == 'OJS_PASSWORD':
                    if force_settings or not current_value:
                        new_value = getpass.getpass(f"{Colors.PURPLE}{Colors.BOLD}? {description}: {Colors.RESET}")
                    else:
                        use_existing = input(f"{Colors.PURPLE}{Colors.BOLD}? Use existing password? (y/n): {Colors.RESET}").lower() == 'y'
                        if not use_existing:
                            new_value = getpass.getpass(f"{Colors.PURPLE}{Colors.BOLD}? {description}: {Colors.RESET}")
                        else:
                            new_value = current_value
                elif key == 'OJS_BASE_URL':
                    # Offer default URL
                    default_url = "https://your-ojs-instance.example.com"
                    if current_value and not force_settings:
                        prompt_text = f"{description} [{current_value}] (press Enter to keep current): "
                        new_value = input(f"{Colors.PURPLE}{Colors.BOLD}? {prompt_text}{Colors.RESET}").strip()
                        new_value = new_value if new_value else current_value
                    else:
                        prompt_text = f"{description} [{default_url}] (press Enter for default): "
                        new_value = input(f"{Colors.PURPLE}{Colors.BOLD}? {prompt_text}{Colors.RESET}").strip()
                        new_value = new_value if new_value else default_url
                else:
                    if current_value and not force_settings:
                        prompt_text = f"{description} [{current_value}] (press Enter to keep current): "
                        new_value = input(f"{Colors.PURPLE}{Colors.BOLD}? {prompt_text}{Colors.RESET}").strip()
                        new_value = new_value if new_value else current_value
                    else:
                        # Show current value as default when reconfiguring (except for password)
                        if current_value and force_settings:
                            prompt_text = f"{description} [{current_value}] (press Enter to keep current): "
                            new_value = input(f"{Colors.PURPLE}{Colors.BOLD}? {prompt_text}{Colors.RESET}").strip()
                            new_value = new_value if new_value else current_value
                        else:
                            new_value = input(f"{Colors.PURPLE}{Colors.BOLD}? {description}: {Colors.RESET}").strip()
                
                if new_value:
                    self.config[key] = new_value
            
            self.save_config()
            print(f"{Colors.GREEN}✓ Configuration saved to .env{Colors.RESET}")
        
        return self.config

class OJSAutomation:
    """OJS automation combining REST API and Web API"""
    
    def __init__(self, config, debug=False):
        self.base_url = config['OJS_BASE_URL'].rstrip('/')
        self.api_token = config['OJS_API_TOKEN']
        self.username = config['OJS_USERNAME']
        self.password = config['OJS_PASSWORD']
        self.debug = debug
        
        # REST API session
        self.rest_session = requests.Session()
        self.rest_session.headers.update({
            'Authorization': f'Bearer {self.api_token}',
            'Accept': 'application/json',
            'User-Agent': 'OJS-Enhanced-Automation/1.0'
        })
        
        # Web API session
        self.web_session = requests.Session()
        self.web_session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        
        self.logged_in = False
    
    def debug_print(self, message):
        """Print debug message only if debug mode is enabled"""
        if self.debug:
            print(message)
    
    def test_rest_api(self):
        """Test REST API connection"""
        try:
            response = self.rest_session.get(f"{self.base_url}/api/v1/submissions")
            if response.status_code in [200, 401]:
                return True
            return False
        except Exception:
            return False
    
    def web_login(self):
        """Login to OJS Web interface"""
        if self.logged_in:
            return True
        
        try:
            # Get login page
            login_url = f"{self.base_url}/login"
            response = self.web_session.get(login_url)
            
            if response.status_code != 200:
                return False
            
            # Parse login form
            soup = BeautifulSoup(response.text, 'html.parser')
            login_form = soup.find('form', {'id': 'login'}) or soup.find('form')
            
            if not login_form:
                return False
            
            # Extract hidden fields
            form_data = {}
            for hidden_input in login_form.find_all('input', type='hidden'):
                name = hidden_input.get('name')
                value = hidden_input.get('value', '')
                if name:
                    form_data[name] = value
            
            # Add credentials
            form_data.update({
                'username': self.username,
                'password': self.password,
                'remember': '0'
            })
            
            # Submit login
            login_action = login_form.get('action', '/login')
            if not login_action.startswith('http'):
                login_action = f"{self.base_url}{login_action}"
            
            response = self.web_session.post(login_action, data=form_data, allow_redirects=True)
            
            # Check success
            success = ('dashboard' in response.url.lower() or 
                      'submissions' in response.url.lower() or
                      'login' not in response.url.lower())
            
            self.logged_in = success
            return success
            
        except Exception as e:
            print(f"{Colors.RED}Web login failed: {e}{Colors.RESET}")
            return False
    
    def get_submission_info(self, submission_id):
        """Get submission and publication info via REST API"""
        try:
            url = f"{self.base_url}/api/v1/submissions/{submission_id}"
            response = self.rest_session.get(url)
            
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None
    
    def get_existing_galleys(self, submission_id):
        """Get existing galleys for submission"""
        submission = self.get_submission_info(submission_id)
        if not submission:
            return None, None, []
        
        current_pub_id = submission.get('currentPublicationId')
        publications = submission.get('publications', [])
        
        current_publication = None
        for pub in publications:
            if pub.get('id') == current_pub_id:
                current_publication = pub
                break
        
        if not current_publication and publications:
            current_publication = publications[0]
            current_pub_id = current_publication.get('id')
        
        galleys = current_publication.get('galleys', []) if current_publication else []
        
        return submission, current_pub_id, galleys
    
    def create_galley_web_api(self, submission_id, publication_id, label, locale="en_US"):
        """Create galley using Web API"""
        if not self.web_login():
            return False
        
        try:
            # Get form
            add_galley_url = f"{self.base_url}/$$$call$$$/grid/article-galleys/article-galley-grid/add-galley"
            params = {
                'submissionId': submission_id,
                'publicationId': publication_id
            }
            
            response = self.web_session.get(add_galley_url, params=params)
            if response.status_code != 200:
                return False
            
            # Parse form
            try:
                json_response = response.json()
                html_content = json_response.get('content', '')
            except:
                html_content = response.text
            
            soup = BeautifulSoup(html_content, 'html.parser')
            csrf_input = soup.find('input', {'name': 'csrfToken'})
            
            if not csrf_input:
                return False
            
            csrf_token = csrf_input.get('value')
            
            # Submit creation
            create_url = f"{self.base_url}/$$$call$$$/grid/article-galleys/article-galley-grid/update-galley"
            params = {
                'submissionId': submission_id,
                'publicationId': publication_id,
                'representationId': ''
            }
            
            form_data = {
                'csrfToken': csrf_token,
                'label': label,
                'galleyLocale': locale
            }
            
            response = self.web_session.post(create_url, params=params, data=form_data)
            
            if response.status_code == 200:
                try:
                    json_response = response.json()
                    return json_response.get('status') == True
                except:
                    return 'error' not in response.text.lower()
            
            return False
            
        except Exception:
            return False
    
    def upload_file_rest_api(self, submission_id, file_path, file_stage=10, genre_id=1, galley_id=None, source_file_id=None):
        """Upload file using REST API"""
        try:
            url = f"{self.base_url}/api/v1/submissions/{submission_id}/files"
            
            with open(file_path, 'rb') as f:
                files = {
                    'file': (file_path.name, f, self._get_mime_type(file_path))
                }
                data = {
                    'fileStage': file_stage,
                    'genreId': genre_id
                }
                
                if galley_id:
                    data['assocType'] = 521  # ASSOC_TYPE_REPRESENTATION
                    data['assocId'] = galley_id
                
                # For dependent files, reference the source file
                if source_file_id:
                    data['sourceSubmissionFileId'] = source_file_id
                
                self.debug_print(f"{Colors.GRAY}[DEBUG] Upload data: {data}{Colors.RESET}")
                response = self.rest_session.post(url, files=files, data=data)
            
            if response.status_code in [200, 201]:
                result = response.json()
                self.debug_print(f"{Colors.GRAY}[DEBUG] Upload response: file ID {result.get('id')}, sourceSubmissionFileId: {result.get('sourceSubmissionFileId')}{Colors.RESET}")
                return result
            else:
                self.debug_print(f"{Colors.RED}[DEBUG] Upload failed: {response.status_code} - {response.text}{Colors.RESET}")
            return None
                
        except Exception as e:
            self.debug_print(f"{Colors.RED}[DEBUG] Upload exception: {e}{Colors.RESET}")
            return None
    
    def get_submission_files(self, submission_id):
        """Get all files for a submission via REST API"""
        try:
            url = f"{self.base_url}/api/v1/submissions/{submission_id}/files"
            response = self.rest_session.get(url)
            
            if response.status_code == 200:
                return response.json()
            return None
        except Exception:
            return None
    
    def _get_mime_type(self, file_path):
        """Get MIME type for file"""
        ext = file_path.suffix.lower()
        mime_types = {
            '.pdf': 'application/pdf',
            '.html': 'text/html',
            '.htm': 'text/html',
            '.css': 'text/css',
            '.gif': 'image/gif',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.zip': 'application/zip',
            '.r': 'text/plain',
            '.do': 'text/plain',
            '.sps': 'application/octet-stream'
        }
        return mime_types.get(ext, 'application/octet-stream')
    
    def get_galley_files(self, submission_id):
        """Get detailed information about files in each galley"""
        submission = self.get_submission_info(submission_id)
        if not submission:
            return {}
        
        galley_files = {}
        publications = submission.get('publications', [])
        
        for pub in publications:
            galleys = pub.get('galleys', [])
            
            for galley in galleys:
                galley_label = galley.get('label', 'Unknown')
                galley_id = galley.get('id')
                
                # Get the main file
                main_file = galley.get('file')
                files_info = []
                
                if main_file:
                    files_info.append({
                        'name': main_file.get('name', {}).get('en_US', 'Unknown'),
                        'id': main_file.get('id'),
                        'mimetype': main_file.get('mimetype', 'unknown'),
                        'type': 'main'
                    })
                
                # Get dependent files
                dependent_files = main_file.get('dependentFiles', []) if main_file else []
                
                for dep_file in dependent_files:
                    dep_name = dep_file.get('name', {}).get('en_US', 'Unknown')
                    files_info.append({
                        'name': dep_name,
                        'id': dep_file.get('id'),
                        'mimetype': dep_file.get('mimetype', 'unknown'),
                        'type': 'dependent'
                    })
                
                # Sort files naturally by name
                files_info.sort(key=lambda x: natural_sort_key(x['name']))
                
                galley_files[galley_label] = {
                    'id': galley_id,
                    'files': files_info
                }
        
        return galley_files
    
    def check_galleys_have_content(self, submission_id):
        """Check if any galleys already have content"""
        galley_files = self.get_galley_files(submission_id)
        if not galley_files:
            return False
        
        # Check if any galley has files
        for galley_label, galley_info in galley_files.items():
            if galley_info['files']:  # If galley has any files
                return True
        
        return False

    def get_main_file_id_for_galley(self, submission_id, galley_label):
        """Get the main file ID for a specific galley"""
        galley_files = self.get_galley_files(submission_id)
        if not galley_files or galley_label not in galley_files:
            return None
        
        galley_info = galley_files[galley_label]
        for file_info in galley_info['files']:
            if file_info['type'] == 'main':
                return file_info['id']
        
        return None

    def analyze_file_conflicts(self, files_found, submission_id):
        """Analyze which files would conflict with existing online files"""
        galley_files = self.get_galley_files(submission_id)
        if not galley_files:
            return {'conflicts': {}, 'new_files': [], 'can_add_to_existing': {}}
        
        conflicts = {}
        new_files = []
        can_add_to_existing = {}
        
        # Get all existing filenames by galley
        existing_files_by_galley = {}
        for galley_label, galley_info in galley_files.items():
            existing_files_by_galley[galley_label.upper()] = [f['name'].lower() for f in galley_info['files']]
        
        # Check PDF
        if files_found['online_pdf']:
            pdf_name = files_found['online_pdf'].name
            if 'PDF' in existing_files_by_galley:
                existing_names = existing_files_by_galley['PDF']
                if any(pdf_name.lower() == existing or 
                       pdf_name.lower() in existing or 
                       existing in pdf_name.lower() for existing in existing_names):
                    conflicts['PDF'] = {
                        'local_file': pdf_name,
                        'existing_files': [f['name'] for f in galley_files['PDF']['files']],
                        'type': 'PDF'
                    }
                else:
                    new_files.append({
                        'file': files_found['online_pdf'],
                        'galley_label': 'PDF',
                        'genre_id': 1,
                        'description': 'OnlinePDF file (new)'
                    })
            else:
                new_files.append({
                    'file': files_found['online_pdf'],
                    'galley_label': 'PDF',
                    'genre_id': 1,
                    'description': 'OnlinePDF file (new galley)'
                })
        
        # Check HTML and figures
        if files_found['html']:
            html_name = files_found['html'].name
            if 'HTML' in existing_files_by_galley:
                existing_names = existing_files_by_galley['HTML']
                # Check if HTML file conflicts
                html_conflicts = any(html_name.lower() == existing or 
                                   html_name.lower() in existing or 
                                   existing in html_name.lower() for existing in existing_names)
                
                if html_conflicts:
                    conflicts['HTML'] = {
                        'local_file': html_name,
                        'existing_files': [f['name'] for f in galley_files['HTML']['files'] if f['type'] == 'main'],
                        'type': 'HTML main file'
                    }
                else:
                    new_files.append({
                        'file': files_found['html'],
                        'galley_label': 'HTML',
                        'genre_id': 1,
                        'description': 'HTML file (additional)'
                    })
                
                # Check CSS files
                new_css = []
                conflicting_css = []
                for css in files_found['css_files']:
                    css_name = css.name.lower()
                    if any(css_name == existing or 
                           css_name in existing or 
                           existing in css_name for existing in existing_names):
                        conflicting_css.append(css)
                    else:
                        new_css.append(css)
                
                # Check figures - these can usually be added even if HTML exists
                new_figures = []
                conflicting_figures = []
                for fig in files_found['figures']:
                    fig_name = fig.name.lower()
                    if any(fig_name == existing or 
                           fig_name in existing or 
                           existing in fig_name for existing in existing_names):
                        conflicting_figures.append(fig)
                    else:
                        new_figures.append(fig)
                
                if conflicting_css or conflicting_figures:
                    if 'HTML' not in conflicts:
                        conflicts['HTML'] = {'local_file': '', 'existing_files': [], 'type': 'HTML dependent files'}
                    if conflicting_css:
                        conflicts['HTML']['conflicting_css'] = [f.name for f in conflicting_css]
                    if conflicting_figures:
                        conflicts['HTML']['conflicting_figures'] = [f.name for f in conflicting_figures]
                
                if new_css or new_figures:
                    can_add_to_existing['HTML'] = {
                        'description': f'{len(new_css + new_figures)} new dependent files can be added to existing HTML galley'
                    }
                    if new_css:
                        can_add_to_existing['HTML']['css'] = new_css
                    if new_figures:
                        can_add_to_existing['HTML']['figures'] = new_figures
                    
            else:
                # No HTML galley exists
                new_files.append({
                    'file': files_found['html'],
                    'galley_label': 'HTML',
                    'genre_id': 1,
                    'description': 'HTML file (new galley)'
                })
                for css in files_found['css_files']:
                    new_files.append({
                        'file': css,
                        'galley_label': 'HTML',
                        'genre_id': 11,
                        'description': 'CSS for HTML'
                    })
                for fig in files_found['figures']:
                    new_files.append({
                        'file': fig,
                        'galley_label': 'HTML',
                        'genre_id': 10,
                        'description': 'Figure for HTML'
                    })
        
        elif (files_found['figures'] or files_found['css_files']) and 'HTML' in existing_files_by_galley:
            # We have CSS/figures but no local HTML, but HTML galley exists online
            existing_names = existing_files_by_galley['HTML']
            new_css = []
            conflicting_css = []
            new_figures = []
            conflicting_figures = []
            
            for css in files_found['css_files']:
                css_name = css.name.lower()
                if any(css_name == existing or 
                       css_name in existing or 
                       existing in css_name for existing in existing_names):
                    conflicting_css.append(css)
                else:
                    new_css.append(css)
            
            for fig in files_found['figures']:
                fig_name = fig.name.lower()
                if any(fig_name == existing or 
                       fig_name in existing or 
                       existing in fig_name for existing in existing_names):
                    conflicting_figures.append(fig)
                else:
                    new_figures.append(fig)
            
            if new_css or new_figures:
                can_add_to_existing['HTML'] = {
                    'description': f'{len(new_css + new_figures)} dependent files can be added to existing HTML galley (no local HTML needed)'
                }
                if new_css:
                    can_add_to_existing['HTML']['css'] = new_css
                if new_figures:
                    can_add_to_existing['HTML']['figures'] = new_figures
            
            if conflicting_css or conflicting_figures:
                conflicts['HTML'] = {
                    'local_file': '',
                    'existing_files': [f['name'] for f in galley_files['HTML']['files']],
                    'type': 'HTML dependent files'
                }
                if conflicting_css:
                    conflicts['HTML']['conflicting_css'] = [f.name for f in conflicting_css]
                if conflicting_figures:
                    conflicts['HTML']['conflicting_figures'] = [f.name for f in conflicting_figures]
        
        # Check other file types (replication, appendix)
        for file_list, galley_label, genre_id, description in [
            (files_found['replication_files'], 'Replication Files', 3, 'Replication file'),
            (files_found['appendix_files'], 'Online Appendix', 12, 'Appendix file')
        ]:
            galley_key = galley_label.upper()
            for file_path in file_list:
                file_name = file_path.name
                if galley_key in existing_files_by_galley:
                    existing_names = existing_files_by_galley[galley_key]
                    if any(file_name.lower() == existing or 
                           file_name.lower() in existing or 
                           existing in file_name.lower() for existing in existing_names):
                        if galley_label not in conflicts:
                            conflicts[galley_label] = {
                                'local_file': '',
                                'existing_files': [f['name'] for f in galley_files[galley_label]['files']],
                                'type': galley_label,
                                'conflicting_files': []
                            }
                        if 'conflicting_files' not in conflicts[galley_label]:
                            conflicts[galley_label]['conflicting_files'] = []
                        conflicts[galley_label]['conflicting_files'].append(file_name)
                    else:
                        new_files.append({
                            'file': file_path,
                            'galley_label': galley_label,
                            'genre_id': genre_id,
                            'description': f'{description} (additional)'
                        })
                else:
                    new_files.append({
                        'file': file_path,
                        'galley_label': galley_label,
                        'genre_id': genre_id,
                        'description': f'{description} (new galley)'
                    })
        
        return {
            'conflicts': conflicts,
            'new_files': new_files,
            'can_add_to_existing': can_add_to_existing
        }
    
    def delete_submission_file(self, submission_id, file_id, stage_id=5):
        """Delete a submission file using REST API
        
        Args:
            submission_id: The submission ID
            file_id: The submission file ID to delete
            stage_id: The workflow stage ID (default 5 = WORKFLOW_STAGE_ID_PRODUCTION)
        """
        try:
            url = f"{self.base_url}/api/v1/submissions/{submission_id}/files/{file_id}"
            params = {'stageId': stage_id}
            
            self.debug_print(f"{Colors.GRAY}[DEBUG] Deleting file ID {file_id} from submission {submission_id}, stage {stage_id}{Colors.RESET}")
            response = self.rest_session.delete(url, params=params)
            
            if response.status_code in [200, 204]:
                self.debug_print(f"{Colors.GRAY}[DEBUG] Successfully deleted file ID {file_id}{Colors.RESET}")
                return True
            else:
                self.debug_print(f"{Colors.RED}[DEBUG] Delete failed: {response.status_code} - {response.text}{Colors.RESET}")
                return False
                
        except Exception as e:
            self.debug_print(f"{Colors.RED}[DEBUG] Delete exception: {e}{Colors.RESET}")
            return False
    
    def find_file_id_by_name(self, submission_id, filename, galley_label):
        """Find file ID by filename within a specific galley"""
        galley_files = self.get_galley_files(submission_id)
        if not galley_files or galley_label not in galley_files:
            return None
        
        galley_info = galley_files[galley_label]
        for file_info in galley_info['files']:
            if file_info['name'] == filename:
                return file_info['id']
        
        return None

    def upload_dependent_file(self, submission_id, file_path, file_stage=17, genre_id=1, source_file_id=None):
        """Upload a dependent file using REST API with correct fileStage for dependent files
        
        Uses fileStage 17 (SUBMISSION_FILE_DEPENDENT) which allows association with other submission files.
        For dependent files, only sourceSubmissionFileId is needed to link to the parent file.
        """
        try:
            url = f"{self.base_url}/api/v1/submissions/{submission_id}/files"
            
            with open(file_path, 'rb') as f:
                files = {
                    'file': (file_path.name, f, self._get_mime_type(file_path))
                }
                data = {
                    'fileStage': file_stage,  # Use fileStage 17 (SUBMISSION_FILE_DEPENDENT) for dependent files
                    'genreId': genre_id
                }
                
                # For dependent files, reference the source file using both approaches
                if source_file_id:
                    data['assocType'] = 515  # ASSOC_TYPE_SUBMISSION_FILE (dependent files)
                    data['assocId'] = source_file_id  # Link to the main file
                    data['sourceSubmissionFileId'] = source_file_id
                
                self.debug_print(f"{Colors.GRAY}[DEBUG] Dependent file upload data: {data}{Colors.RESET}")
                response = self.rest_session.post(url, files=files, data=data)
            
            if response.status_code in [200, 201]:
                result = response.json()
                self.debug_print(f"{Colors.GRAY}[DEBUG] Dependent file upload response: file ID {result.get('id')}, sourceSubmissionFileId: {result.get('sourceSubmissionFileId')}{Colors.RESET}")
                return result
            else:
                # Enhanced error reporting with detailed server response analysis
                error_detail = ""
                try:
                    # Try to parse JSON error response
                    if response.headers.get('content-type', '').startswith('application/json'):
                        error_json = response.json()
                        if 'error' in error_json:
                            error_detail = f" - {error_json['error']}"
                        elif 'errorMessage' in error_json:
                            error_detail = f" - {error_json['errorMessage']}"
                        elif 'message' in error_json:
                            error_detail = f" - {error_json['message']}"
                        else:
                            # Show the full JSON structure for debugging
                            error_detail = f" - {error_json}"
                    else:
                        # Handle HTML or text error responses
                        error_text = response.text[:500]  # Get more text for better debugging
                        if 'Fatal error' in error_text:
                            # Extract PHP fatal error
                            import re
                            match = re.search(r'Fatal error: (.+?) in', error_text)
                            if match:
                                error_detail = f" - Fatal error: {match.group(1)}"
                            else:
                                error_detail = f" - {error_text}"
                        else:
                            error_detail = f" - {error_text}"
                except Exception as parse_error:
                    error_detail = f" - Raw response: {response.text[:200]} (Parse error: {parse_error})"
                
                self.debug_print(f"{Colors.RED}[DEBUG] Dependent file upload failed: HTTP {response.status_code}{error_detail}{Colors.RESET}")
                print(f"{Colors.RED}ERROR: Failed to upload dependent file {file_path.name}: HTTP {response.status_code}{error_detail}{Colors.RESET}")
            return None
                
        except Exception as e:
            self.debug_print(f"{Colors.RED}[DEBUG] Dependent file upload exception: {e}{Colors.RESET}")
            print(f"{Colors.RED}ERROR: Exception uploading dependent file {file_path.name}: {e}{Colors.RESET}")
            return None
    
    def verify_file_upload(self, submission_id, file_id, stage_id=1, max_retries=3, retry_delay=2):
        """
        Verify that a file upload was successful by checking if the file exists via API.
        This helps ensure main files are fully processed before uploading dependent files.
        
        Args:
            submission_id: The submission ID
            file_id: The file ID to verify
            stage_id: The workflow stage ID (required by OJS API)
            max_retries: Maximum number of verification attempts
            retry_delay: Seconds to wait between retries
            
        Returns:
            bool: True if file exists and is accessible, False otherwise
        """
        import time
        
        for attempt in range(max_retries):
            try:
                url = f"{self.base_url}/api/v1/submissions/{submission_id}/files/{file_id}"
                params = {'stageId': stage_id}  # Required parameter according to API docs
                response = self.rest_session.get(url, params=params)
                
                if response.status_code == 200:
                    file_data = response.json()
                    self.debug_print(f"{Colors.GRAY}[DEBUG] File verification successful: {file_id} exists{Colors.RESET}")
                    return True
                elif response.status_code == 404:
                    self.debug_print(f"{Colors.GRAY}[DEBUG] File verification attempt {attempt + 1}/{max_retries}: file {file_id} not yet available{Colors.RESET}")
                elif response.status_code == 403:
                    self.debug_print(f"{Colors.GRAY}[DEBUG] File verification attempt {attempt + 1}/{max_retries}: access denied (HTTP 403), trying different stageId{Colors.RESET}")
                    # Try with a different stageId (production stage)
                    params = {'stageId': 5}
                    response = self.rest_session.get(url, params=params)
                    if response.status_code == 200:
                        file_data = response.json()
                        self.debug_print(f"{Colors.GRAY}[DEBUG] File verification successful with stageId=5: {file_id} exists{Colors.RESET}")
                        return True
                else:
                    self.debug_print(f"{Colors.GRAY}[DEBUG] File verification attempt {attempt + 1}/{max_retries}: HTTP {response.status_code}{Colors.RESET}")
                
                if attempt < max_retries - 1:  # Don't wait after the last attempt
                    time.sleep(retry_delay)
                    
            except Exception as e:
                self.debug_print(f"{Colors.GRAY}[DEBUG] File verification attempt {attempt + 1}/{max_retries} failed: {e}{Colors.RESET}")
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
        
        self.debug_print(f"{Colors.RED}[DEBUG] File verification failed after {max_retries} attempts for file {file_id}{Colors.RESET}")
        return False
    
    def extract_pages_from_pdf(self, pdf_path):
        """Extract page numbers from the first page of a PDF file"""
        try:
            self.debug_print(f"  {Colors.GRAY}DEBUG: Extracting pages from {pdf_path}{Colors.RESET}")
            
            # Open PDF document
            doc = fitz.open(pdf_path)
            
            if len(doc) == 0:
                self.debug_print(f"  {Colors.GRAY}DEBUG: PDF has no pages{Colors.RESET}")
                return None
            
            # Get first page text
            first_page = doc[0]
            text = first_page.get_text()
            doc.close()
            
            self.debug_print(f"  {Colors.GRAY}DEBUG: First page text (first 200 chars): {text[:200]}{Colors.RESET}")
            
            # Pattern to match: "Vol. X, No. Y, pp. Z-W" or similar variations
            # Handle different dash types (ASCII -, en-dash –, em-dash —)
            patterns = [
                r'Vol\.\s*\d+,\s*No\.\s*\d+,\s*pp\.\s*(\d+[-–—]\d+)',  # Standard format
                r'pp\.\s*(\d+[-–—]\d+)',  # Just pp. X-Y
                r'Pages?\s*(\d+[-–—]\d+)',  # Pages X-Y
                r'S\.\s*(\d+[-–—]\d+)',  # German: S. X-Y (Seiten)
            ]
            
            for pattern in patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    page_range = match.group(1)
                    # Convert any type of dash to ASCII dash for OJS
                    page_range_ascii = re.sub(r'[–—]', '-', page_range)
                    self.debug_print(f"  {Colors.GRAY}DEBUG: Found page range: {page_range} -> {page_range_ascii}{Colors.RESET}")
                    return page_range_ascii
            
            self.debug_print(f"  {Colors.GRAY}DEBUG: No page range pattern found in PDF text{Colors.RESET}")
            return None
            
        except Exception as e:
            print(f"{Colors.YELLOW}⚠ Could not extract pages from PDF: {e}{Colors.RESET}")
            return None
    
    def update_publication_pages(self, submission_id, publication_id, pages):
        """Update only the pages field of a publication"""
        try:
            self.debug_print(f"  {Colors.GRAY}DEBUG: Updating publication {publication_id} pages to: {pages}{Colors.RESET}")
            
            # Only send the pages field to avoid modifying other data
            data = {
                'pages': pages
            }
            
            response = self.rest_session.put(
                f"{self.base_url}/api/v1/submissions/{submission_id}/publications/{publication_id}",
                json=data
            )
            
            if response.status_code == 200:
                self.debug_print(f"  {Colors.GRAY}DEBUG: Successfully updated publication pages{Colors.RESET}")
                return True
            else:
                self.debug_print(f"  {Colors.GRAY}DEBUG: Failed to update publication pages: HTTP {response.status_code}{Colors.RESET}")
                if self.debug:
                    self.debug_print(f"  {Colors.GRAY}DEBUG: Response: {response.text}{Colors.RESET}")
                return False
                
        except Exception as e:
            print(f"{Colors.RED}Error updating publication pages: {e}{Colors.RESET}")
            return False

def parse_submission_input(input_value):
    """Parse input to determine if it's a submission ID or a path
    
    Returns:
        tuple: (submission_id, folder_path) where folder_path is None if not found
    """
    # Try to parse as integer (submission ID)
    try:
        submission_id = int(input_value)
        return submission_id, None
    except ValueError:
        pass
    
    # Try to parse as path
    path = Path(input_value)
    if path.exists() and path.is_dir():
        # Extract submission ID from folder name
        folder_name = path.name
        # Look for 4 or 5-digit numbers in the folder name (submission IDs)
        import re
        # Use negative lookbehind/lookahead to ensure we don't match part of a longer number
        submission_id_match = re.search(r'(?<![0-9])(\d{4,5})(?![0-9])', folder_name)
        if submission_id_match:
            submission_id = int(submission_id_match.group(1))
            print(f"{Colors.GREEN}✓ Using folder: {path}{Colors.RESET}")
            print(f"{Colors.GREEN}✓ Extracted submission ID: {submission_id}{Colors.RESET}")
            return submission_id, path
        else:
            print(f"{Colors.RED}✗ Could not extract submission ID from folder name: {folder_name}{Colors.RESET}")
            print(f"{Colors.GRAY}Expected a 4 or 5-digit number in the folder name{Colors.RESET}")
            return None, None
    else:
        print(f"{Colors.RED}✗ Path does not exist or is not a directory: {input_value}{Colors.RESET}")
        return None, None

def find_submission_folder(submission_id, skip=False):
    """Find folder containing submission ID
    Returns tuple: (folder_path, user_selected)
    user_selected=True if user chose from multiple options, False if auto-found
    """
    current_dir = Path('.')
    
    matching_folders = []
    for folder in current_dir.iterdir():
        if folder.is_dir() and str(submission_id) in folder.name:
            matching_folders.append(folder)
    
    if not matching_folders:
        print(f"{Colors.RED}✗ No folder found containing submission ID {submission_id}{Colors.RESET}")
        return None, False
    
    if len(matching_folders) == 1:
        folder_path = matching_folders[0]
        print(f"{Colors.GREEN}✓ Found folder: {folder_path}{Colors.RESET}")
        return folder_path, False  # Auto-found, needs confirmation
    
    # Multiple folders found
    if skip:
        # When --skip is enabled, terminate instead of asking user to choose
        print(f"{Colors.RED}✗ Multiple folders found containing '{submission_id}' and --skip is enabled:{Colors.RESET}")
        for i, folder in enumerate(matching_folders, 1):
            print(f"{Colors.RED}  {i}. {folder}{Colors.RESET}")
        print(f"{Colors.RED}✗ Cannot auto-select folder when multiple options exist. Please specify the exact folder path.{Colors.RESET}")
        return None, False
    
    # Multiple folders - let user choose (only when --skip is not enabled)
    print(f"{Colors.YELLOW}Multiple folders found containing '{submission_id}':{Colors.RESET}")
    for i, folder in enumerate(matching_folders, 1):
        print(f"{Colors.BLUE}  {i}. {folder}{Colors.RESET}")
    
    while True:
        try:
            choice = input(f"{Colors.PURPLE}{Colors.BOLD}\n? Select folder (number): {Colors.RESET}")
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(matching_folders):
                folder_path = matching_folders[choice_idx]
                print(f"\n{Colors.GREEN}✓ Selected folder: {folder_path}{Colors.RESET}")
                return folder_path, True  # User selected, no additional confirmation needed
            else:
                print(f"{Colors.RED}Invalid choice. Please enter 1-{len(matching_folders)}{Colors.RESET}")
        except (ValueError, KeyboardInterrupt):
            print(f"\n{Colors.YELLOW}Operation cancelled{Colors.RESET}")
            return None, False

def extract_files_from_zip(zip_path, submission_id, temp_dir):
    """Extract relevant files from a zip archive"""
    extracted_files = {
        'online_pdf': None,
        'html': None,
        'figures': [],
        'appendix_files': []
    }
    
    try:
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            for file_info in zip_ref.filelist:
                filename = file_info.filename.lower()
                
                # OnlinePDF: srm_####_OnlinePDF.pdf
                if f'srm_{submission_id}_onlinepdf.pdf' in filename:
                    extract_path = temp_dir / Path(file_info.filename).name
                    with zip_ref.open(file_info) as source, open(extract_path, 'wb') as target:
                        shutil.copyfileobj(source, target)
                    extracted_files['online_pdf'] = extract_path
                
                # HTML: srm_####.html
                elif f'srm_{submission_id}.html' in filename:
                    extract_path = temp_dir / Path(file_info.filename).name
                    with zip_ref.open(file_info) as source, open(extract_path, 'wb') as target:
                        shutil.copyfileobj(source, target)
                    extracted_files['html'] = extract_path
                
                # Figures: srm_####_Fig*_HTML.gif (only HTML versions for online)
                elif (f'srm_{submission_id}_fig' in filename and 
                      '_html.gif' in filename):
                    extract_path = temp_dir / Path(file_info.filename).name
                    with zip_ref.open(file_info) as source, open(extract_path, 'wb') as target:
                        shutil.copyfileobj(source, target)
                    extracted_files['figures'].append(extract_path)
                
                # Appendix: 800000_<year>_####_MOESM*_ESM.pdf
                elif ('moesm' in filename and 'esm.pdf' in filename and 
                      str(submission_id) in filename):
                    extract_path = temp_dir / Path(file_info.filename).name
                    with zip_ref.open(file_info) as source, open(extract_path, 'wb') as target:
                        shutil.copyfileobj(source, target)
                    extracted_files['appendix_files'].append(extract_path)
    
    except Exception as e:
        print(f"{Colors.YELLOW}⚠ Could not extract from {zip_path.name}: {e}{Colors.RESET}")
    
    return extracted_files

def analyze_folder_files(folder_path, submission_id):
    """Analyze files in folder according to SRM patterns, including zip files"""
    files_found = {
        'online_pdf': None,
        'html': None,
        'figures': [],
        'css_files': [],
        'replication_files': [],
        'appendix_files': [],
        'temp_dir': None  # Track temp directory for cleanup
    }
    
    print(f"\n{Colors.CYAN}/{folder_path}/{Colors.RESET}")
    
    # Check if there's a main submission zip file
    submission_zip = None
    for file_path in folder_path.iterdir():
        if (file_path.is_file() and file_path.suffix.lower() == '.zip' and 
            str(submission_id) in file_path.name):
            submission_zip = file_path
            break
    
    # If we found a submission zip, extract files from it
    if submission_zip:
        print(f"{Colors.BLUE}Found submission zip: {submission_zip.name}{Colors.RESET}")
        temp_dir = Path(tempfile.mkdtemp())
        files_found['temp_dir'] = temp_dir
        
        extracted = extract_files_from_zip(submission_zip, submission_id, temp_dir)
        files_found.update({k: v for k, v in extracted.items() if v})
        
        print(f"{Colors.GREEN}Extracted {sum(1 if v else len(v) if isinstance(v, list) else 0 for v in extracted.values())} files from zip{Colors.RESET}")
    
    # Scan the folder for additional files (CSS, replication, etc.)
    for file_path in folder_path.rglob('*'):
        if file_path.is_file():
            filename = file_path.name.lower()
            
            # Skip the submission zip file (already processed)
            if submission_zip and file_path == submission_zip:
                continue
            
            # OnlinePDF: srm_####_OnlinePDF.pdf (if not found in zip)
            if (not files_found['online_pdf'] and 
                f'srm_{submission_id}_onlinepdf.pdf' in filename):
                files_found['online_pdf'] = file_path
            
            # HTML: srm_####.html (if not found in zip)
            elif (not files_found['html'] and 
                  f'srm_{submission_id}.html' in filename):
                files_found['html'] = file_path
            
            # Figures: srm_####_Fig*.gif (if not found in zip)
            elif (f'srm_{submission_id}_fig' in filename and 
                  filename.endswith('.gif')):
                # Only add if not already extracted from zip
                if not any(fig.name.lower() == filename for fig in files_found['figures']):
                    files_found['figures'].append(file_path)
            
            # CSS files: any .css file
            elif filename.endswith('.css'):
                files_found['css_files'].append(file_path)
            
            # Replication files: various formats
            elif 'replication' in filename and filename.endswith(('.zip', '.r', '.do', '.sps')):
                files_found['replication_files'].append(file_path)
            
            # Appendix: 800000_<year>_####_MOESM*_ESM.pdf (if not found in zip)
            elif ('moesm' in filename and 'esm.pdf' in filename and 
                  str(submission_id) in filename):
                # Only add if not already extracted from zip
                if not any(app.name.lower() == filename for app in files_found['appendix_files']):
                    files_found['appendix_files'].append(file_path)
    
    # Display file tree
    print(f"{Colors.CYAN}└── Submission {submission_id}{Colors.RESET}")
    
    # PDF section
    if files_found['online_pdf']:
        print(f"    {Colors.GREEN}├── PDF{Colors.RESET}")
        print(f"    │   └── {Colors.GREEN}{files_found['online_pdf'].name}{Colors.RESET}")
    else:
        print(f"    {Colors.RED}├── PDF (missing){Colors.RESET}")
    
    # HTML section
    if files_found['html']:
        has_figures = len(files_found['figures']) > 0
        has_css = len(files_found['css_files']) > 0
        total_html_deps = len(files_found['figures']) + len(files_found['css_files'])
        
        print(f"    {Colors.GREEN}├── HTML{Colors.RESET}")
        print(f"    │   ├── {Colors.GREEN}{files_found['html'].name}{Colors.RESET}")
        
        if has_css:
            if has_figures or total_html_deps == len(files_found['css_files']):
                print(f"    │   ├── {Colors.GREEN}CSS Files ({len(files_found['css_files'])} files){Colors.RESET}")
            else:
                print(f"    │   └── {Colors.GREEN}CSS Files ({len(files_found['css_files'])} files){Colors.RESET}")
            for i, css in enumerate(sorted(files_found['css_files'], key=lambda x: natural_sort_key(x.name))):
                is_last_css = i == len(files_found['css_files']) - 1
                connector = "└──" if is_last_css and not has_figures else "├──"
                print(f"    │   │   {connector} {Colors.GREEN}{css.name}{Colors.RESET}")
        
        if has_figures:
            print(f"    │   └── {Colors.GREEN}Figures ({len(files_found['figures'])} files){Colors.RESET}")
            for i, fig in enumerate(sorted(files_found['figures'], key=lambda x: natural_sort_key(x.name))):
                is_last = i == len(files_found['figures']) - 1
                connector = "└──" if is_last else "├──"
                print(f"    │       {connector} {Colors.GREEN}{fig.name}{Colors.RESET}")
        elif not has_css:
            print(f"    │   └── {Colors.YELLOW}No CSS or figures found{Colors.RESET}")
    else:
        if files_found['figures'] or files_found['css_files']:
            orphaned_count = len(files_found['figures']) + len(files_found['css_files'])
            print(f"    {Colors.YELLOW}├── HTML (missing, but {orphaned_count} dependent files found!){Colors.RESET}")
            for css in sorted(files_found['css_files'], key=lambda x: natural_sort_key(x.name)):
                print(f"    │   └── {Colors.YELLOW}{css.name} (orphaned CSS){Colors.RESET}")
            for fig in sorted(files_found['figures'], key=lambda x: natural_sort_key(x.name)):
                print(f"    │   └── {Colors.YELLOW}{fig.name} (orphaned figure){Colors.RESET}")
        else:
            print(f"    {Colors.RED}├── HTML (missing){Colors.RESET}")
    
    # Replication files
    if files_found['replication_files']:
        print(f"    {Colors.GREEN}├── Replication Files ({len(files_found['replication_files'])} files){Colors.RESET}")
        for i, repl in enumerate(files_found['replication_files']):
            is_last = i == len(files_found['replication_files']) - 1
            connector = "└──" if is_last else "├──"
            print(f"    │   {connector} {Colors.GREEN}{repl.name}{Colors.RESET}")
    else:
        print(f"    {Colors.YELLOW}├── Replication Files (none){Colors.RESET}")
    
    # Appendix files
    if files_found['appendix_files']:
        print(f"    {Colors.GREEN}└── Online Appendix ({len(files_found['appendix_files'])} files){Colors.RESET}")
        for i, app in enumerate(files_found['appendix_files']):
            is_last = i == len(files_found['appendix_files']) - 1
            connector = "└──" if is_last else "├──"
            print(f"        {connector} {Colors.GREEN}{app.name}{Colors.RESET}")
    else:
        print(f"    {Colors.YELLOW}└── Online Appendix (none){Colors.RESET}")
    
    # Summary
    total_files = sum([
        1 if files_found['online_pdf'] else 0,
        1 if files_found['html'] else 0,
        len(files_found['figures']),
        len(files_found['css_files']),
        len(files_found['replication_files']),
        len(files_found['appendix_files'])
    ])
    
    print(f"\n{Colors.CYAN}Summary: {total_files} files found{Colors.RESET}")
    
    # Warnings
    dependent_files_count = len(files_found['figures']) + len(files_found['css_files'])
    if dependent_files_count > 0 and not files_found['html']:
        print(f"{Colors.YELLOW}⚠ Warning: Found {dependent_files_count} dependent files (CSS/figures) but no HTML file{Colors.RESET}")
    
    # Sort all file lists for consistent behavior
    files_found['figures'].sort(key=lambda x: natural_sort_key(x.name))
    files_found['css_files'].sort(key=lambda x: natural_sort_key(x.name))
    files_found['replication_files'].sort(key=lambda x: natural_sort_key(x.name))
    files_found['appendix_files'].sort(key=lambda x: natural_sort_key(x.name))
    
    return files_found

def cleanup_temp_files(files_found):
    """Clean up temporary files if they were created"""
    if files_found.get('temp_dir'):
        try:
            shutil.rmtree(files_found['temp_dir'])
            print(f"{Colors.GRAY}Cleaned up temporary files{Colors.RESET}")
        except Exception as e:
            print(f"{Colors.YELLOW}⚠ Could not clean up temporary files: {e}{Colors.RESET}")

def create_upload_plan(files_found, existing_galleys):
    """Create upload plan based on found files and existing galleys"""
    plan = {
        'galleys_to_create': [],
        'uploads': []
    }
    
    # Map existing galleys
    galley_map = {g.get('label', '').upper(): g for g in existing_galleys}
    
    print(f"\n{Colors.CYAN}{Colors.UNDERLINE}Upload Plan{Colors.RESET}")
    
    # PDF file
    if files_found['online_pdf']:
        if 'PDF' not in galley_map:
            plan['galleys_to_create'].append('PDF')
            print(f"  {Colors.YELLOW}Will create PDF galley{Colors.RESET}")
        else:
            print(f"  {Colors.GREEN}Using existing PDF galley{Colors.RESET}")
        
        print(f"  {Colors.GREEN}1 file to PDF galley{Colors.RESET}")
        print(f"    - {Colors.GREEN}{files_found['online_pdf'].name}{Colors.RESET}")
        plan['uploads'].append({
            'file': files_found['online_pdf'],
            'galley_label': 'PDF',
            'genre_id': 1,  # Article Text
            'description': 'OnlinePDF file'
        })
    
    # HTML + CSS + figures
    if files_found['html']:
        if 'HTML' not in galley_map:
            plan['galleys_to_create'].append('HTML')
            print(f"  {Colors.YELLOW}Will create HTML galley{Colors.RESET}")
        else:
            print(f"  {Colors.GREEN}Using existing HTML galley{Colors.RESET}")
        
        total_html_files = 1 + len(files_found['css_files']) + len(files_found['figures'])
        print(f"  {Colors.GREEN}{total_html_files} files to HTML galley{Colors.RESET}")
        print(f"    - {Colors.GREEN}{files_found['html'].name}{Colors.RESET}")
        
        plan['uploads'].append({
            'file': files_found['html'],
            'galley_label': 'HTML',
            'genre_id': 1,  # Article Text
            'description': 'HTML file'
        })
        
        # Add CSS files as dependencies
        if files_found['css_files']:
            for css in sorted(files_found['css_files'], key=lambda x: natural_sort_key(x.name)):
                print(f"    - {Colors.GREEN}{css.name}{Colors.RESET}")
                plan['uploads'].append({
                    'file': css,
                    'galley_label': 'HTML',
                    'genre_id': 11,  # HTML Stylesheet
                    'description': 'CSS for HTML'
                })
        
        # Add figures as dependencies
        if files_found['figures']:
            for fig in sorted(files_found['figures'], key=lambda x: natural_sort_key(x.name)):
                print(f"    - {Colors.GREEN}{fig.name}{Colors.RESET}")
                plan['uploads'].append({
                    'file': fig,
                    'galley_label': 'HTML',
                    'genre_id': 10,  # Image
                    'description': 'Figure for HTML'
                })
    
    # Replication files
    if files_found['replication_files']:
        if 'REPLICATION FILES' not in galley_map:
            plan['galleys_to_create'].append('Replication Files')
            print(f"  {Colors.YELLOW}Will create Replication Files galley{Colors.RESET}")
        else:
            print(f"  {Colors.GREEN}Using existing Replication Files galley{Colors.RESET}")
        
        print(f"  {Colors.GREEN}{len(files_found['replication_files'])} files to Replication Files galley{Colors.RESET}")
        for repl in files_found['replication_files']:
            print(f"    - {Colors.GREEN}{repl.name}{Colors.RESET}")
            plan['uploads'].append({
                'file': repl,
                'galley_label': 'Replication Files',
                'genre_id': 3,  # Research Materials
                'description': 'Replication file'
            })
    
    # Appendix files
    if files_found['appendix_files']:
        if 'ONLINE APPENDIX' not in galley_map:
            plan['galleys_to_create'].append('Online Appendix')
            print(f"  {Colors.YELLOW}Will create Online Appendix galley{Colors.RESET}")
        else:
            print(f"  {Colors.GREEN}Using existing Online Appendix galley{Colors.RESET}")
        
        print(f"  {Colors.GREEN}{len(files_found['appendix_files'])} files to Online Appendix galley{Colors.RESET}")
        for app in files_found['appendix_files']:
            print(f"    - {Colors.GREEN}{app.name}{Colors.RESET}")
            plan['uploads'].append({
                'file': app,
                'galley_label': 'Online Appendix',
                'genre_id': 12,  # Appendix
                'description': 'Appendix file'
            })
    
    return plan

def create_overwrite_plan(files_found, existing_galleys, conflicts):
    """Create upload plan that includes deleting conflicting files first"""
    plan = {
        'galleys_to_create': [],
        'deletions': [],
        'uploads': []
    }
    
    # Map existing galleys
    galley_map = {g.get('label', '').upper(): g for g in existing_galleys}
    
    print(f"\n{Colors.CYAN}{Colors.UNDERLINE}Overwrite Plan{Colors.RESET}")
    
    # Handle PDF
    if files_found['online_pdf']:
        if 'PDF' not in galley_map:
            plan['galleys_to_create'].append('PDF')
            print(f"  {Colors.YELLOW}Will create PDF galley{Colors.RESET}")
        elif 'PDF' in conflicts:
            print(f"  {Colors.RED}Will delete existing PDF file(s) and upload new one{Colors.RESET}")
            # Add existing files to deletion list
            for existing_file in conflicts['PDF']['existing_files']:
                plan['deletions'].append({
                    'galley_label': 'PDF',
                    'filename': existing_file,
                    'type': 'main'
                })
        else:
            print(f"  {Colors.GREEN}Using existing PDF galley{Colors.RESET}")
        
        print(f"  {Colors.GREEN}1 file to PDF galley{Colors.RESET}")
        print(f"    - {Colors.GREEN}{files_found['online_pdf'].name}{Colors.RESET}")
        plan['uploads'].append({
            'file': files_found['online_pdf'],
            'galley_label': 'PDF',
            'genre_id': 1,
            'description': 'OnlinePDF file'
        })
    
    # Handle HTML, CSS, and figures
    if files_found['html']:
        if 'HTML' not in galley_map:
            plan['galleys_to_create'].append('HTML')
            print(f"  {Colors.YELLOW}Will create HTML galley{Colors.RESET}")
        elif 'HTML' in conflicts:
            print(f"  {Colors.RED}Will delete existing HTML file(s) and upload new ones{Colors.RESET}")
            # Add existing files to deletion list
            for existing_file in conflicts['HTML']['existing_files']:
                plan['deletions'].append({
                    'galley_label': 'HTML',
                    'filename': existing_file,
                    'type': 'main'
                })
            if 'conflicting_figures' in conflicts['HTML']:
                for fig_name in conflicts['HTML']['conflicting_figures']:
                    plan['deletions'].append({
                        'galley_label': 'HTML',
                        'filename': fig_name,
                        'type': 'dependent'
                    })
            if 'conflicting_css' in conflicts['HTML']:
                for css_name in conflicts['HTML']['conflicting_css']:
                    plan['deletions'].append({
                        'galley_label': 'HTML',
                        'filename': css_name,
                        'type': 'dependent'
                    })
        else:
            print(f"  {Colors.GREEN}Using existing HTML galley{Colors.RESET}")
        
        total_html_files = 1 + len(files_found['css_files']) + len(files_found['figures'])
        print(f"  {Colors.GREEN}{total_html_files} files to HTML galley{Colors.RESET}")
        print(f"    - {Colors.GREEN}{files_found['html'].name}{Colors.RESET}")
        
        plan['uploads'].append({
            'file': files_found['html'],
            'galley_label': 'HTML',
            'genre_id': 1,
            'description': 'HTML file'
        })
        
        # Add CSS files
        if files_found['css_files']:
            for css in sorted(files_found['css_files'], key=lambda x: natural_sort_key(x.name)):
                print(f"    - {Colors.GREEN}{css.name}{Colors.RESET}")
                plan['uploads'].append({
                    'file': css,
                    'galley_label': 'HTML',
                    'genre_id': 11,
                    'description': 'CSS for HTML'
                })
        
        # Add figures
        if files_found['figures']:
            for fig in sorted(files_found['figures'], key=lambda x: natural_sort_key(x.name)):
                print(f"    - {Colors.GREEN}{fig.name}{Colors.RESET}")
                plan['uploads'].append({
                    'file': fig,
                    'galley_label': 'HTML',
                    'genre_id': 10,
                    'description': 'Figure for HTML'
                })
    
    # Handle other file types
    for file_list, galley_label, genre_id, description in [
        (files_found['replication_files'], 'Replication Files', 3, 'Replication file'),
        (files_found['appendix_files'], 'Online Appendix', 12, 'Appendix file')
    ]:
        if file_list:
            galley_key = galley_label.upper()
            if galley_key not in galley_map:
                plan['galleys_to_create'].append(galley_label)
                print(f"  {Colors.YELLOW}Will create {galley_label} galley{Colors.RESET}")
            elif galley_label in conflicts and 'conflicting_files' in conflicts[galley_label]:
                print(f"  {Colors.RED}Will delete existing {galley_label} file(s) and upload new ones{Colors.RESET}")
                for existing_file in conflicts[galley_label]['conflicting_files']:
                    plan['deletions'].append({
                        'galley_label': galley_label,
                        'filename': existing_file,
                        'type': 'main'
                    })
            else:
                print(f"  {Colors.GREEN}Using existing {galley_label} galley{Colors.RESET}")
            
            print(f"  {Colors.GREEN}{len(file_list)} files to {galley_label} galley{Colors.RESET}")
            for file_path in file_list:
                print(f"    - {Colors.GREEN}{file_path.name}{Colors.RESET}")
                plan['uploads'].append({
                    'file': file_path,
                    'galley_label': galley_label,
                    'genre_id': genre_id,
                    'description': description
                })
    
    # Show deletion summary
    if plan['deletions']:
        print(f"\n  {Colors.RED}Files to delete: {len(plan['deletions'])}{Colors.RESET}")
        # Sort deletions for better readability: main files first, then dependent files
        sorted_deletions = sorted(plan['deletions'], key=lambda x: (x['galley_label'], 'main' not in x.get('type', ''), natural_sort_key(x['filename'])))
        for deletion in sorted_deletions:
            print(f"    - {Colors.RED}{deletion['filename']} (from {deletion['galley_label']}){Colors.RESET}")
    
    return plan

def create_selective_upload_plan(new_files, can_add_to_existing, existing_galleys):
    """Create upload plan for only non-conflicting files"""
    plan = {
        'galleys_to_create': [],
        'uploads': []
    }
    
    # Map existing galleys
    galley_map = {g.get('label', '').upper(): g for g in existing_galleys}
    
    print(f"\n{Colors.CYAN}{Colors.UNDERLINE}Selective Upload Plan{Colors.RESET}")
    
    # Add regular new files
    galleys_needed = set()
    for file_info in new_files:
        galley_label = file_info['galley_label']
        if galley_label.upper() not in galley_map:
            galleys_needed.add(galley_label)
        plan['uploads'].append(file_info)
    
    # Add files that can be added to existing galleys
    for galley_label, add_info in can_add_to_existing.items():
        if 'css' in add_info:
            for css in sorted(add_info['css'], key=lambda x: natural_sort_key(x.name)):
                plan['uploads'].append({
                    'file': css,
                    'galley_label': galley_label,
                    'genre_id': 11,  # HTML Stylesheet
                    'description': 'CSS for existing HTML galley',
                    'is_dependent': True  # Mark as dependent file
                })
        if 'figures' in add_info:
            for fig in sorted(add_info['figures'], key=lambda x: natural_sort_key(x.name)):
                plan['uploads'].append({
                    'file': fig,
                    'galley_label': galley_label,
                    'genre_id': 10,  # Image
                    'description': 'Figure for existing HTML galley',
                    'is_dependent': True  # Mark as dependent file
                })
    
    # Add galleys that need to be created
    plan['galleys_to_create'] = list(galleys_needed)
    
    # Show what will be done
    if plan['galleys_to_create']:
        print(f"  {Colors.YELLOW}Will create galleys: {', '.join(plan['galleys_to_create'])}{Colors.RESET}")
    
    uploads_by_galley = {}
    for upload in plan['uploads']:
        galley_label = upload['galley_label']
        if galley_label not in uploads_by_galley:
            uploads_by_galley[galley_label] = []
        uploads_by_galley[galley_label].append(upload['file'].name)
    
    for galley_label, filenames in uploads_by_galley.items():
        is_existing = galley_label.upper() in galley_map
        status = "existing" if is_existing else "new"
        print(f"  {Colors.YELLOW}{len(filenames)} files to {status} {galley_label} galley{Colors.RESET}")
        for filename in sorted(filenames, key=natural_sort_key):
            print(f"    - {Colors.GREEN}{filename}{Colors.RESET}")
    
    return plan

def show_current_online_files(automation, submission_id):
    """Show current files that are online"""
    print(f"\n{Colors.CYAN}{Colors.UNDERLINE}Current Online Files{Colors.RESET}")
    
    galley_files = automation.get_galley_files(submission_id)
    
    if not galley_files:
        print(f"{Colors.YELLOW}No files currently online{Colors.RESET}")
        return False
    
    print(f"{Colors.GREEN}└── Submission {submission_id} (Online){Colors.RESET}")
    
    galley_labels = list(galley_files.keys())
    total_files = 0
    
    for i, (galley_label, galley_info) in enumerate(galley_files.items()):
        is_last_galley = i == len(galley_labels) - 1
        galley_connector = "└──" if is_last_galley else "├──"
        
        files = galley_info['files']
        total_files += len(files)
        
        if files:
            print(f"    {galley_connector} {Colors.GREEN}{galley_label} Galley{Colors.RESET} {Colors.CYAN}({len(files)} files){Colors.RESET}")
            
            for j, file_info in enumerate(files):
                is_last_file = j == len(files) - 1
                file_connector = "└──" if is_last_file else "├──"
                
                # Color-code file type labels
                if file_info['type'] == 'main':
                    file_type_label = f" {Colors.GREEN}({Colors.UNDERLINE}main{Colors.RESET}{Colors.GREEN}){Colors.RESET}"
                else:
                    file_type_label = f" {Colors.GREEN}({Colors.ITALIC}dep.{Colors.RESET}{Colors.GREEN}){Colors.RESET}"
                
                if is_last_galley:
                    indent = "        "
                else:
                    indent = "    │   "
                
                print(f"{indent}{file_connector} {Colors.GREEN}{file_info['name']}{Colors.RESET}{file_type_label}")
        else:
            print(f"    {galley_connector} {Colors.YELLOW}{galley_label} Galley{Colors.RESET} (empty)")
    
    print(f"\n{Colors.CYAN}Total: {len(galley_files)} galleys with {total_files} files online{Colors.RESET}")
    return total_files > 0

def show_final_status(automation, submission_id):
    """Show final status after upload"""
    print(f"\n{Colors.CYAN}{Colors.UNDERLINE}Final Status{Colors.RESET}")
    
    galley_files = automation.get_galley_files(submission_id)
    
    if not galley_files:
        print(f"{Colors.RED}Could not retrieve final status{Colors.RESET}")
        return
    
    # Show debug info if debug mode is enabled
    debug_mode = hasattr(automation, 'debug') and automation.debug
    if debug_mode:
        # Get submission info for debug details
        submission = automation.get_submission_info(submission_id)
        if submission:
            publications = submission.get('publications', [])
            print(f"{Colors.GRAY}Publications count: {len(publications)}{Colors.RESET}")
            if publications:
                pub = publications[0]  # Use first publication
                galleys = pub.get('galleys', [])
                print(f"{Colors.GRAY}Publication {pub.get('id')} has {len(galleys)} galleys{Colors.RESET}")
    
    print(f"{Colors.GREEN}└── Submission {submission_id} (Online){Colors.RESET}")
    
    galley_labels = list(galley_files.keys())
    for i, (galley_label, galley_info) in enumerate(galley_files.items()):
        is_last_galley = i == len(galley_labels) - 1
        galley_connector = "└──" if is_last_galley else "├──"
        
        files = galley_info['files']
        print(f"    {galley_connector} {Colors.GREEN}{galley_label} Galley{Colors.RESET} {Colors.CYAN}({len(files)} files){Colors.RESET}")
        
        # Debug info for this galley
        if debug_mode:
            galley_id = galley_info.get('id', 'Unknown')
            main_files = [f for f in files if f['type'] == 'main']
            dep_files = [f for f in files if f['type'] == 'dependent']
            
            if is_last_galley:
                debug_indent = "        "
            else:
                debug_indent = "    │   "
            
            print(f"{debug_indent}{Colors.GRAY}Galley ID: {galley_id}{Colors.RESET}")
            if main_files:
                print(f"{debug_indent}{Colors.GRAY}Main file: {main_files[0]['name']}{Colors.RESET}")
            if dep_files:
                print(f"{debug_indent}{Colors.GRAY}Dependent files: {len(dep_files)} files{Colors.RESET}")
        
        for j, file_info in enumerate(files):
            is_last_file = j == len(files) - 1
            file_connector = "└──" if is_last_file else "├──"
            
            # Color-code file type labels
            if file_info['type'] == 'main':
                file_type_label = f" {Colors.GREEN}({Colors.UNDERLINE}main{Colors.RESET}{Colors.GREEN}){Colors.RESET}"
            else:
                file_type_label = f" {Colors.GREEN}({Colors.ITALIC}dep.{Colors.RESET}{Colors.GREEN}){Colors.RESET}"
            
            if is_last_galley:
                indent = "        "
            else:
                indent = "    │   "
            
            print(f"{indent}{file_connector} {Colors.GREEN}{file_info['name']}{Colors.RESET}{file_type_label}")
            
            # Debug info for individual files
            if debug_mode:
                file_id = file_info.get('id', 'Unknown')
                mimetype = file_info.get('mimetype', 'unknown')
                
                debug_file_indent = indent + ("    " if file_connector == "└──" else "│   ")
                print(f"{debug_file_indent}{Colors.GRAY}ID: {file_id}, MIME: {mimetype}{Colors.RESET}")
    
    total_galleys = len(galley_files)
    total_files = sum(len(info['files']) for info in galley_files.values())
    print(f"\n{Colors.GREEN}{total_galleys} galleys with {total_files} total files now online{Colors.RESET}")

def execute_automation(automation, submission_id, publication_id, plan, dry_run=False):
    """Execute the automation plan"""
    print(f"\n{Colors.CYAN}{Colors.UNDERLINE}Execution{Colors.RESET}")
    
    if dry_run:
        print(f"{Colors.BLUE}Dry run mode - no changes will be made{Colors.RESET}")
        
        if 'deletions' in plan and plan['deletions']:
            print(f"\n{Colors.BLUE}Files to delete: {len(plan['deletions'])}{Colors.RESET}")
            for deletion in plan['deletions']:
                print(f"  {Colors.BLUE}- {deletion['filename']} from {deletion['galley_label']} galley{Colors.RESET}")
        
        if plan['galleys_to_create']:
            print(f"\n{Colors.BLUE}Galleys to create: {len(plan['galleys_to_create'])}{Colors.RESET}")
            for galley in plan['galleys_to_create']:
                print(f"  {Colors.BLUE}- {galley}{Colors.RESET}")
        
        if plan['uploads']:
            print(f"\n{Colors.BLUE}Files to upload: {len(plan['uploads'])}{Colors.RESET}")
            for upload in plan['uploads']:
                print(f"  {Colors.BLUE}- {upload['file'].name} to {upload['galley_label']} ({upload['description']}){Colors.RESET}")
        
        # Show page extraction preview even in dry-run mode
        pdf_upload = None
        for upload in plan['uploads']:
            if (upload['galley_label'].upper() == 'PDF' and 
                upload['description'] == 'OnlinePDF file'):
                pdf_upload = upload
                break
        
        if pdf_upload:
            print(f"\n{Colors.BLUE}Would extract pages from PDF...{Colors.RESET}")
            pages = automation.extract_pages_from_pdf(pdf_upload['file'])
            if pages:
                print(f"  {Colors.BLUE}Pages extracted: {pages}{Colors.RESET}")
                print(f"  {Colors.BLUE}Would update publication pages to: {pages}{Colors.RESET}")
            else:
                print(f"  {Colors.BLUE}Could not extract page numbers from PDF{Colors.RESET}")
        
        return True
    
    # Handle deletions first (for overwrite plans)
    if 'deletions' in plan and plan['deletions']:
        print(f"\n{Colors.RED}Deleting conflicting files...{Colors.RESET}")
        deleted_count = 0
        skipped_count = 0
        main_files_deleted = set()  # Track which galleys had main files deleted (for cascading)
        
        # Sort deletions: main files first, then dependent files
        sorted_deletions = sorted(plan['deletions'], key=lambda x: (x['galley_label'], 'main' not in x.get('type', ''), natural_sort_key(x['filename'])))
        
        # Check if we have dependent files that will be auto-deleted
        has_dependent_files = any(deletion.get('type') == 'dependent' for deletion in plan['deletions'])
        if has_dependent_files:
            print(f"{Colors.GRAY}Note: Dependent files (figures) are automatically deleted when main files are removed{Colors.RESET}")
        
        for deletion in sorted_deletions:
            filename = deletion['filename']
            galley_label = deletion['galley_label']
            
            # Skip dependent files if we already deleted the main file from this galley
            if galley_label in main_files_deleted and deletion.get('type') != 'main':
                skipped_count += 1
                continue
            
            print(f"{Colors.YELLOW}Deleting {filename} from {galley_label} galley{Colors.RESET}")
            file_id = automation.find_file_id_by_name(submission_id, filename, galley_label)
            
            if file_id:
                success = automation.delete_submission_file(submission_id, file_id)
                if success:
                    print(f"{Colors.GREEN}Deleted {filename} (ID: {file_id}){Colors.RESET}")
                    deleted_count += 1
                    # Track if this was a main file deletion
                    if deletion.get('type') == 'main' or galley_label in ['HTML', 'PDF']:
                        main_files_deleted.add(galley_label)
                else:
                    print(f"{Colors.RED}Failed to delete {filename}{Colors.RESET}")
            else:
                print(f"{Colors.YELLOW}File {filename} not found for deletion (may have been deleted already){Colors.RESET}")
        
        total_deleted = deleted_count + skipped_count
        if skipped_count > 0:
            print(f"\n{Colors.GREEN}Deleted {total_deleted}/{len(plan['deletions'])} files ({deleted_count} directly, {skipped_count} auto-deleted){Colors.RESET}")
        else:
            print(f"\n{Colors.GREEN}Deleted {deleted_count}/{len(plan['deletions'])} files{Colors.RESET}")
        
        # Wait a moment for deletions to propagate
        if deleted_count > 0:
            time.sleep(2)

    # Create missing galleys
    created_galleys = []
    for galley_label in plan['galleys_to_create']:
        print(f"{Colors.YELLOW}Creating galley: {galley_label}{Colors.RESET}")
        success = automation.create_galley_web_api(submission_id, publication_id, galley_label)
        if success:
            print(f"{Colors.GREEN}Created galley: {galley_label}{Colors.RESET}")
            created_galleys.append(galley_label)
        else:
            print(f"{Colors.RED}Failed to create galley: {galley_label}{Colors.RESET}")
    
    # Get updated galley info after creation
    time.sleep(2)  # Wait for galley creation to propagate
    _, _, updated_galleys = automation.get_existing_galleys(submission_id)
    galley_map = {g.get('label', '').upper(): g for g in updated_galleys}
    
    # Upload files with proper dependent file handling
    uploaded_count = 0
    galley_main_files = {}  # Track main file IDs for each galley
    
    # Group uploads by galley to handle dependencies
    uploads_by_galley = {}
    for upload in plan['uploads']:
        galley_label = upload['galley_label']
        if galley_label not in uploads_by_galley:
            uploads_by_galley[galley_label] = {'main': [], 'figures': []}
        
        # Check if this is marked as dependent or is a figure/CSS
        if (upload.get('is_dependent', False) or 
            upload['description'] in ['Figure for HTML', 'CSS for HTML', 
                                    'Figure for existing HTML galley', 'CSS for existing HTML galley']):
            uploads_by_galley[galley_label]['figures'].append(upload)
        else:
            uploads_by_galley[galley_label]['main'].append(upload)
    
    # Upload files galley by galley
    for galley_label, uploads in uploads_by_galley.items():
        galley = galley_map.get(galley_label.upper())
        galley_id = galley.get('id') if galley else None
        
        print(f"\n{Colors.CYAN}Processing {galley_label} galley:{Colors.RESET}")
        
        # Upload main files first, or get existing main file ID
        main_file_id = None
        
        # If this galley already exists and we're only adding dependent files, get the existing main file ID
        if galley_id and not uploads['main'] and uploads['figures']:
            main_file_id = automation.get_main_file_id_for_galley(submission_id, galley_label)
            if main_file_id:
                print(f"{Colors.BLUE}Using existing main file (ID: {main_file_id}) for dependent files{Colors.RESET}")
        
        # Upload any new main files
        for upload in uploads['main']:
            file_path = upload['file']
            genre_id = upload['genre_id']
            file_name = file_path.name
            
            print(f"{Colors.YELLOW}Uploading {file_name} to {galley_label} (main){Colors.RESET}")
            result = automation.upload_file_rest_api(
                submission_id, file_path, 
                file_stage=10, genre_id=genre_id, galley_id=galley_id
            )
            
            if result:
                uploaded_count += 1
                file_id = result.get('id')
                print(f"{Colors.GREEN}Uploaded {file_name} (ID: {file_id}){Colors.RESET}")
                
                # Track the main file ID for HTML galleys (for dependent files)
                if galley_label.upper() == 'HTML':
                    main_file_id = file_id
                    galley_main_files[galley_label] = main_file_id
                    
                    # Verify main file upload before proceeding with dependent files
                    print(f"{Colors.BLUE}Verifying main file upload...{Colors.RESET}")
                    if not automation.verify_file_upload(submission_id, file_id, stage_id=5):  # Production stage
                        print(f"{Colors.YELLOW}Warning: Main file verification failed, dependent files may not upload correctly{Colors.RESET}")
            else:
                print(f"{Colors.RED}Failed to upload {file_name}{Colors.RESET}")
        
        # Upload dependent files (figures) and link them
        dependent_file_ids = []
        if uploads['figures'] and not main_file_id:
            print(f"{Colors.YELLOW}Warning: No main file ID found for {galley_label}, dependent files may not link correctly{Colors.RESET}")
            
        for upload in uploads['figures']:
            file_path = upload['file']
            genre_id = upload['genre_id']
            file_name = file_path.name
            
            print(f"{Colors.YELLOW}Uploading {file_name} to {galley_label} (dependent){Colors.RESET}")
            # Use special upload method for dependent files
            result = automation.upload_dependent_file(
                submission_id, file_path, 
                file_stage=17, genre_id=genre_id, 
                source_file_id=main_file_id  # Link to main HTML file
            )
            file_id = result.get('id') if result else None
            
            if result:
                uploaded_count += 1
                dependent_file_ids.append(file_id)
                print(f"{Colors.GREEN}Uploaded {file_name} (ID: {file_id}, linked to: {main_file_id}){Colors.RESET}")
            else:
                print(f"{Colors.RED}Failed to upload {file_name}{Colors.RESET}")
        
        # Note: File associations are handled automatically via sourceSubmissionFileId during upload
        # No need for additional Web API calls to set main files or update dependencies
    
    print(f"\n{Colors.GREEN}Completed: {uploaded_count}/{len(plan['uploads'])} files uploaded{Colors.RESET}")
    
    # Update publication pages if we uploaded an OnlinePDF
    pdf_uploaded = False
    pdf_file_path = None
    for upload in plan['uploads']:
        if (upload['galley_label'].upper() == 'PDF' and 
            upload['description'] == 'OnlinePDF file' and
            uploaded_count > 0):
            pdf_uploaded = True
            pdf_file_path = upload['file']
            break
    
    if pdf_uploaded and pdf_file_path:
        print(f"\n{Colors.CYAN}Updating publication pages from PDF...{Colors.RESET}")
        
        # Extract page numbers from PDF
        pages = automation.extract_pages_from_pdf(pdf_file_path)
        
        if pages:
            print(f"{Colors.GREEN}✓ Extracted pages from PDF: {pages}{Colors.RESET}")
            
            # Update publication
            success = automation.update_publication_pages(submission_id, publication_id, pages)
            if success:
                print(f"{Colors.GREEN}✓ Updated publication pages to: {pages}{Colors.RESET}")
            else:
                print(f"{Colors.RED}✗ Failed to update publication pages{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}⚠ Could not extract page numbers from PDF{Colors.RESET}")
            print(f"{Colors.GRAY}  Try running with --debug to see the PDF text content{Colors.RESET}")
    
    return uploaded_count > 0

def show_help():
    help_text = f"""
{Colors.CYAN}{Colors.UNDERLINE}OJA{Colors.RESET}
OJS Automated File Submission
Combines REST API (file uploads) and Web API (galley creation) for automated file submission

{Colors.CYAN}{Colors.UNDERLINE}USAGE:{Colors.RESET}
  {Colors.GREEN}oja{Colors.RESET} {Colors.BLUE}<submission_id_or_path>{Colors.RESET} {Colors.YELLOW}[options]{Colors.RESET}

{Colors.CYAN}{Colors.UNDERLINE}ARGUMENTS:{Colors.RESET}
  {Colors.BLUE}submission_id_or_path{Colors.RESET}    Submission ID (e.g., 8661) or folder path

{Colors.CYAN}{Colors.UNDERLINE}OPTIONS:{Colors.RESET}
  {Colors.YELLOW}--settings{Colors.RESET}             Reconfigure OJS connection settings
  {Colors.YELLOW}--dry-run{Colors.RESET}              Preview file submission without executing
  {Colors.YELLOW}--debug{Colors.RESET}                Show debug information
  {Colors.YELLOW}--skip{Colors.RESET}                 Skip confirmations
  {Colors.YELLOW}--help, -h{Colors.RESET}             Show this help message

{Colors.CYAN}{Colors.UNDERLINE}FILE TYPES SUPPORTED:{Colors.RESET}
  {Colors.GREEN}• PDF{Colors.RESET}                   srm_XXXX_OnlinePDF.pdf
  {Colors.GREEN}• HTML{Colors.RESET}                  srm_XXXX.html with srm_XXXX_FigN_HTML.gif figures
  {Colors.GREEN}• CSS{Colors.RESET}                   stylesheet.css (any .css file)
  {Colors.GREEN}• Replication Files{Colors.RESET}     replication.zip (.zip, .r, .do, .sps with 'replication')
  {Colors.GREEN}• Online Appendix{Colors.RESET}       800000_<year>_XXXX_MOESM*_ESM.pdf

{Colors.CYAN}{Colors.UNDERLINE}BEST PRACTICE FOLDER STRUCTURE:{Colors.RESET}
  submission_folder/      (folder should be named with submission ID (e.g., 'year_8661_author'))
  ├── srm_XXXX.zip        (contains PDF, HTML, figures, appendix)
  ├── stylesheet.css      (outside zip)
  └── replication.zip     (outside zip)

{Colors.CYAN}{Colors.UNDERLINE}CONFIGURATION:{Colors.RESET}
  On first run, you'll be prompted for:
  • OJS Base URL (e.g., https://your-ojs-instance.example.com)
  • API Token (from your OJS user profile)
  • Username and Password (from your OJS user profile)

"""
    print(help_text)

def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='OJS Automation', add_help=False)
    parser.add_argument('submission_id_or_path', nargs='?', help='Submission ID (e.g., 8661) or direct path to submission folder')
    parser.add_argument('--settings', action='store_true', help='Reconfigure settings')
    parser.add_argument('--dry-run', action='store_true', help='Show plan without executing')
    parser.add_argument('--debug', action='store_true', help='Enable debug output')
    parser.add_argument('--skip', action='store_true', help='Skip confirmation prompts')
    parser.add_argument('--help', '-h', action='store_true', help='Show help message')
    
    args = parser.parse_args()
    
    # Handle help
    if args.help or not args.submission_id_or_path:
        show_help()
        return True
    
    try:
        # Parse submission ID or path
        submission_id, folder_path = parse_submission_input(args.submission_id_or_path)
        if submission_id is None:
            return False
        
        # Load/setup configuration
        config_manager = OJSConfig()
        config = config_manager.get_or_prompt_config(args.settings)
        
        # Initialize automation
        automation = OJSAutomation(config, debug=args.debug)
        
        # Test connections
        print(f"{Colors.CYAN}Testing connections...{Colors.RESET}")
        if not automation.test_rest_api():
            print(f"{Colors.RED}✗ REST API connection failed{Colors.RESET}")
            print(f"{Colors.GRAY}Try running with --settings to update your API key{Colors.RESET}")
            return False
        
        if not automation.web_login():
            print(f"{Colors.RED}✗ Web login failed{Colors.RESET}")
            print(f"{Colors.GRAY}Try running with --settings to update your credentials{Colors.RESET}")
            return False
        
        print(f"{Colors.GREEN}✓ All connections successful{Colors.RESET}")
        
        # Find submission folder (if not already provided)
        user_selected_folder = False
        if folder_path is None:
            folder_path, user_selected_folder = find_submission_folder(submission_id, skip=args.skip)
            if not folder_path:
                return False
        
        # Confirmation to proceed with found folder (only if auto-found, not user-selected)
        if not args.skip and not user_selected_folder:
            continue_with_folder = input(f"\n{Colors.PURPLE}{Colors.BOLD}? Continue with this folder? (y/n): {Colors.RESET}").lower().strip()
            if continue_with_folder != 'y':
                print(f"\n{Colors.YELLOW}Operation cancelled{Colors.RESET}")
                return False
        elif args.skip and not user_selected_folder:
            print(f"\n{Colors.GREEN}Proceeding with folder (--skip enabled){Colors.RESET}")
        elif user_selected_folder:
            print(f"\n{Colors.GREEN}Proceeding with selected folder{Colors.RESET}")
        
        # Analyze files
        files_found = analyze_folder_files(folder_path, submission_id)
        
        # Get submission info
        submission, publication_id, existing_galleys = automation.get_existing_galleys(submission_id)
        if not submission:
            print(f"{Colors.RED}Could not access submission {submission_id}{Colors.RESET}")
            return False
        
        print(f"{Colors.CYAN}Existing galleys: {len(existing_galleys)}{Colors.RESET}")
        for galley in existing_galleys:
            print(f"{Colors.GREEN}  - {galley.get('label')} (ID: {galley.get('id')}){Colors.RESET}")
        
        # Show current online files
        has_online_content = show_current_online_files(automation, submission_id)
        
        # Analyze file conflicts and create refined plan
        if has_online_content:
            print(f"\n{Colors.CYAN}Analyzing potential conflicts...{Colors.RESET}")
            conflict_analysis = automation.analyze_file_conflicts(files_found, submission_id)
            
            conflicts = conflict_analysis['conflicts']
            new_files = conflict_analysis['new_files']
            can_add_to_existing = conflict_analysis['can_add_to_existing']
            
            # Show detailed analysis
            if conflicts:
                print(f"\n{Colors.RED}⚠ CONFLICTS DETECTED:{Colors.RESET}")
                for galley_label, conflict_info in conflicts.items():
                    print(f"\n{Colors.CYAN}  {galley_label} Galley:{Colors.RESET}")
                    if conflict_info['local_file']:
                        print(f"    {Colors.RED}{conflict_info['local_file']}{Colors.RESET} would conflict with:")
                        for existing in conflict_info['existing_files']:
                            print(f"      - {existing}")
                    
                    if 'conflicting_css' in conflict_info:
                        print(f"    {Colors.RED}Conflicting CSS files:{Colors.RESET}")
                        for css in sorted(conflict_info['conflicting_css'], key=natural_sort_key):
                            print(f"      - {css}")
                    
                    if 'conflicting_figures' in conflict_info:
                        print(f"    {Colors.RED}Conflicting figures:{Colors.RESET}")
                        for fig in sorted(conflict_info['conflicting_figures'], key=natural_sort_key):
                            print(f"      - {fig}")
                    
                    if 'conflicting_files' in conflict_info:
                        print(f"    {Colors.RED}Conflicting files:{Colors.RESET}")
                        for file_name in sorted(conflict_info['conflicting_files'], key=natural_sort_key):
                            print(f"      - {file_name}")
            
            if new_files or can_add_to_existing:
                print(f"\n{Colors.GREEN}✓ NON-CONFLICTING CONTENT:{Colors.RESET}")
                
                if new_files:
                    grouped_new_files = {}
                    for file_info in new_files:
                        galley = file_info['galley_label']
                        if galley not in grouped_new_files:
                            grouped_new_files[galley] = []
                        grouped_new_files[galley].append(file_info)
                    
                    for galley_label, files in grouped_new_files.items():
                        print(f"\n{Colors.CYAN}  {galley_label} Galley:{Colors.RESET}")
                        # Sort files naturally by filename
                        sorted_files = sorted(files, key=lambda x: natural_sort_key(x['file'].name))
                        for file_info in sorted_files:
                            print(f"    {Colors.GREEN}OK: {file_info['file'].name}{Colors.RESET} - {file_info['description']}")
                
                if can_add_to_existing:
                    for galley_label, add_info in can_add_to_existing.items():
                        print(f"\n{Colors.CYAN}  {galley_label} Galley:{Colors.RESET}")
                        print(f"    {Colors.GREEN}OK: {add_info['description']}{Colors.RESET}")
                        if 'css' in add_info:
                            for css in sorted(add_info['css'], key=lambda x: natural_sort_key(x.name)):
                                print(f"      - {css.name}")
                        if 'figures' in add_info:
                            for fig in sorted(add_info['figures'], key=lambda x: natural_sort_key(x.name)):
                                print(f"      - {fig.name}")
            
            # Provide options
            options = []
            option_descriptions = []
            
            if new_files or can_add_to_existing:
                options.append('n')
                # Count all non-conflicting files
                total_non_conflicting = len(new_files)
                for info in can_add_to_existing.values():
                    total_non_conflicting += len(info.get('css', []))
                    total_non_conflicting += len(info.get('figures', []))
                
                option_descriptions.append(f"  {Colors.GREEN}n{Colors.RESET} - Upload only NON-conflicting files ({total_non_conflicting} files)")
            
            if conflicts:
                options.extend(['o', 'c'])
                option_descriptions.extend([
                    f"  {Colors.BLUE}o{Colors.RESET} - Overwrite conflicting files with new versions",
                    f"  {Colors.RED}c{Colors.RESET} - Cancel operation"
                ])
            else:
                if new_files or can_add_to_existing:
                    options.append('c')
                    option_descriptions.append(f"  {Colors.RED}c{Colors.RESET} - Cancel operation")
                else:
                    print(f"\n{Colors.YELLOW}No new files to upload.{Colors.RESET}")
                    return False
            
            # Print options header and all options
            if options:
                print(f"\n{Colors.PURPLE}Options:{Colors.RESET}")
                for description in option_descriptions:
                    print(description)
            
            if args.skip:
                # Auto-choose: prefer non-conflicting ('n') if available, otherwise overwrite ('o')
                if 'n' in options:
                    choice = 'n'
                    print(f"\n{Colors.GREEN}Auto-choosing: Upload only non-conflicting files (--skip enabled){Colors.RESET}")
                elif 'o' in options:
                    choice = 'o'
                    print(f"\n{Colors.GREEN}Auto-choosing: Overwrite conflicting files (--skip enabled){Colors.RESET}")
                else:
                    print(f"\n{Colors.YELLOW}No viable upload option available{Colors.RESET}")
                    return False
            else:
                while True:
                    choice = input(f"\n{Colors.PURPLE}{Colors.BOLD}? Choose ({'/'.join(options)}): {Colors.RESET}").lower().strip()
                    if choice in options:
                        break
                    else:
                        print(f"{Colors.RED}Invalid choice. Please enter one of: {'/'.join(options)}{Colors.RESET}")
            
            if choice == 'n' and 'n' in options:
                print(f"{Colors.GREEN}Uploading only non-conflicting files...{Colors.RESET}")
                # Create plan with only non-conflicting files
                plan = create_selective_upload_plan(new_files, can_add_to_existing, existing_galleys)
                
                # Confirmation prompt for selective upload
                if not args.dry_run and plan['uploads'] and not args.skip:
                    print(f"\n{Colors.CYAN}Ready to proceed with selective upload plan.{Colors.RESET}")
                    continue_upload = input(f"\n{Colors.PURPLE}{Colors.BOLD}? Continue with upload? (y/n): {Colors.RESET}").lower().strip()
                    if continue_upload != 'y':
                        print(f"{Colors.YELLOW}Upload cancelled{Colors.RESET}")
                        return False
                elif args.skip and not args.dry_run and plan['uploads']:
                    print(f"\n{Colors.GREEN}Proceeding with selective upload (--skip enabled){Colors.RESET}")
            elif choice == 'o' and 'o' in options:
                print(f"{Colors.BLUE}Overwriting conflicting files...{Colors.RESET}")
                # Create overwrite plan
                plan = create_overwrite_plan(files_found, existing_galleys, conflicts)
                
                # Confirmation prompt for overwrite
                if not args.dry_run and plan['uploads'] and not args.skip:
                    print(f"\n{Colors.CYAN}Ready to proceed with overwrite plan.{Colors.RESET}")
                    continue_upload = input(f"\n{Colors.PURPLE}{Colors.BOLD}? Continue with overwrite? (y/n): {Colors.RESET}").lower().strip()
                    if continue_upload != 'y':
                        print(f"{Colors.YELLOW}Overwrite cancelled{Colors.RESET}")
                        return False
                elif args.skip and not args.dry_run and plan['uploads']:
                    print(f"\n{Colors.GREEN}Proceeding with overwrite (--skip enabled){Colors.RESET}")
            elif choice == 'c' and 'c' in options:
                print(f"\n{Colors.YELLOW}Operation cancelled{Colors.RESET}")
                return False
        else:
            # No existing content, create normal plan
            plan = create_upload_plan(files_found, existing_galleys)
            
            # Confirmation prompt for new submissions (no existing content)
            if not args.dry_run and plan['uploads'] and not args.skip:
                print(f"\n{Colors.CYAN}Ready to proceed with upload plan.{Colors.RESET}")
                continue_upload = input(f"\n{Colors.PURPLE}{Colors.BOLD}? Continue with upload? (y/n): {Colors.RESET}").lower().strip()
                if continue_upload != 'y':
                    print(f"{Colors.YELLOW}Upload cancelled{Colors.RESET}")
                    return False
            elif args.skip and not args.dry_run and plan['uploads']:
                print(f"\n{Colors.GREEN}Proceeding with upload (--skip enabled){Colors.RESET}")
            elif not plan['uploads']:
                print(f"{Colors.YELLOW}No files to upload. Nothing to do.{Colors.RESET}")
                return False
        
        # Execute automation
        success = execute_automation(
            automation=automation, 
            submission_id=submission_id, 
            publication_id=publication_id, 
            plan=plan, 
            dry_run=args.dry_run
        )
        
        # Show final status (only if not dry run and upload was successful)
        if not args.dry_run and success:
            show_final_status(automation, submission_id)
        
        # Clean up temporary files
        cleanup_temp_files(files_found)
        
        return success
        
    except KeyboardInterrupt:
        print(f"\n{Colors.YELLOW}Interrupted{Colors.RESET}")
        # Try to clean up even if interrupted
        if 'files_found' in locals():
            cleanup_temp_files(files_found)
        return False
    except Exception as e:
        print(f"\n{Colors.RED}Error: {e}{Colors.RESET}")
        # Try to clean up even if error occurred
        if 'files_found' in locals():
            cleanup_temp_files(files_found)
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)