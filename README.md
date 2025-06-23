# OJA - OJS Automated File Submission

A Python tool that combines REST API (file uploads) and Web API (galley creation) for automated file submission to Open Journal Systems (OJS).

## Features

- **Automated galley creation** - Creates PDF and HTML galleys automatically
- **Automated file upload** - Handles all file types with proper association
- **Page number extraction** - Extracts page numbers from PDF and updates publication details
- **Conflict detection** - Analyzes existing files and prevents overwrites
- **Dry-run mode** - Preview submissions without executing
- **Smart file detection** - Recognizes files from folder structure or zip archives

## Installation

### Requirements
- Python 3.7+
- Access to an OJS instance with API enabled

### Setup

1. Clone this repository:
```bash
git clone https://github.com/johxyz/oja
cd oja
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Install the package:
```bash
pip install -e .
```

## Usage

### Basic Usage
```bash
oja <submission_id_or_path> [options]
```

### Examples
```bash
# Submit by submission ID (searches for folder automatically)
oja 8661

# Submit by folder path
oja /path/to/submission_folder/
oja ./12-23_8661_author/

# Preview without uploading
oja 8661 --dry-run

# Show debug information
oja 8661 --debug

# Skip all confirmations
oja 8661 --skip

# Reconfigure settings
oja --settings
```

### Command Options

| Option | Description |
|--------|-------------|
| `--settings` | Reconfigure OJS connection settings |
| `--dry-run` | Preview file submission without executing |
| `--debug` | Show debug information |
| `--skip` | Skip confirmations |
| `--help`, `-h` | Show help message |

## Configuration

On first run, you'll be prompted for:

- **OJS Base URL** (e.g., `https://your-ojs-instance.example.com`)
- **API Token** (from your OJS user profile → API Key)
- **Username** (your OJS login username)
- **Password** (your OJS login password)

Configuration is saved globally to `~/.config/oja/config.env` for use from any directory.

## File Structure & Naming Conventions

### Supported File Types

| Type | Description | Naming Pattern |
|------|-------------|----------------|
| **PDF** | Main article PDF | `srm_XXXX_OnlinePDF.pdf` |
| **HTML** | HTML version | `srm_XXXX.html` |
| **Figures** | HTML figures | `srm_XXXX_FigN_HTML.gif` |
| **CSS** | Stylesheets | `stylesheet.css` (any `.css` file) |
| **Replication** | Data/code files | `replication.zip`, `*.r`, `*.do`, `*.sps` |
| **Appendix** | Online appendix | `800000_<year>_XXXX_MOESM*_ESM.pdf` |

### Folder Structure Options

#### Option 1: Zip-based Structure (Recommended)
```
submission_folder/                       # Named with submission ID (e.g., '2024_8661_author')
├── srm_XXXX.zip                         # Main submission archive containing:
│   ├── srm_XXXX_OnlinePDF.pdf           #   - PDF version
│   ├── srm_XXXX.html                    #   - HTML version  
│   ├── srm_XXXX_Fig1_HTML.gif           #   - Figure 1
│   ├── srm_XXXX_Fig2_HTML.gif           #   - Figure 2
│   └── 800000_2024_XXXX_MOESM1_ESM.pdf  #   - Appendix files
├── stylesheet.css                       # CSS files (outside zip)
└── replication.zip                      # Replication files (outside zip)
```

#### Option 2: Flat Structure
```
submission_folder/
├── srm_XXXX_OnlinePDF.pdf
├── srm_XXXX.html
├── srm_XXXX_Fig1_HTML.gif
├── srm_XXXX_Fig2_HTML.gif
├── stylesheet.css
├── replication.zip
└── 800000_2024_XXXX_MOESM1_ESM.pdf
```

### File Naming Details

#### PDF Files
- **Pattern**: `srm_<submission_id>_OnlinePDF.pdf`
- **Example**: `srm_8661_OnlinePDF.pdf`
- **Purpose**: Main article PDF for the PDF galley

#### HTML Files
- **Pattern**: `srm_<submission_id>.html`
- **Example**: `srm_8661.html`
- **Purpose**: HTML version of the article

#### Figure Files  
- **Pattern**: `srm_<submission_id>_Fig<N>_HTML.gif`
- **Examples**: 
  - `srm_8661_Fig1_HTML.gif`
  - `srm_8661_Fig10_HTML.gif`
  - `srm_8661_Fig2b_HTML.gif`
- **Purpose**: Figures referenced in HTML version
- **Note**: Figures are automatically sorted using natural sorting (Fig1, Fig2, Fig10...)

#### CSS Files
- **Pattern**: Any file ending in `.css`
- **Examples**: 
  - `stylesheet.css`
  - `custom.css`
  - `journal.css`
- **Purpose**: Styling for HTML galley

#### Replication Files
- **Patterns**: Files containing "replication" in the name
  - `replication.zip`
  - `replication_data.r`
  - `replication_analysis.do`
  - `replication_code.sps`
- **Purpose**: Data and code files for research reproducibility

#### Appendix Files
- **Pattern**: `800000_<year>_<submission_id>_MOESM<N>_ESM.pdf`
- **Examples**:
  - `800000_2024_8661_MOESM1_ESM.pdf`
  - `800000_2024_8661_MOESM2_ESM.pdf`
- **Purpose**: Online appendix materials

### Galley Assignment

The tool automatically creates and assigns files to appropriate galleys:

- **PDF Galley**: Contains the main PDF file
- **HTML Galley**: Contains HTML file, figures, and CSS files  
- **Replication Galley**: Contains replication files
- **Online Appendix Galley**: Contains appendix PDF files

## How It Works

1. **File Discovery**: Scans folder or extracts from zip to find files
2. **Pattern Matching**: Identifies file types based on naming conventions
3. **Conflict Analysis**: Compares with existing online files
4. **Galley Management**: Creates new galleys or uses existing ones
5. **File Upload**: Uploads files using OJS REST API
6. **Association**: Links files to appropriate galleys
7. **Verification**: Confirms successful uploads

## Conflict Resolution

When files already exist online, the tool provides options:

- **Upload non-conflicting only**: Safely add new files without overwriting
- **Overwrite conflicts**: Replace existing files with new versions  
- **Cancel**: Abort the submission to resolve conflicts manually

## Best Practices

### Folder Naming
- Include submission ID in folder name (e.g., `20-34_8661_author`)
- Use consistent ID format for easy identification

### File Organization
- Use zip files for the main submission bundle
- Keep CSS and replication files outside the zip
- Follow exact naming conventions for automatic detection

### Before Submission
- Run with `--dry-run` to preview the upload plan
- Check that all expected files are detected
- Verify file naming follows the patterns exactly

### Troubleshooting
- Use `--debug` to see detailed file analysis
- Run `oja --settings` to reconfigure if connection fails (no submission ID needed)
- Check `~/.config/oja/config.env` for configuration issues
- Verify submission ID exists and is accessible in OJS

## Dependencies

- `requests>=2.32.0` - HTTP requests for API calls
- `PyMuPDF>=1.23.0` - PDF processing for page extraction  
- `beautifulsoup4>=4.12.0` - HTML parsing for web interface
- `python-dotenv>=1.0.0` - Environment variable management

## Error Handling

The tool includes error handling for:

- Network connectivity issues
- Authentication failures  
- File upload errors
- API response validation
- Temporary file cleanup

## Security

- Credentials are stored globally in `~/.config/oja/config.env` (separate from your project files)
- API tokens are used for authentication
- No sensitive data is logged in normal operation
- Temporary files are automatically cleaned up

## License

MIT License

Copyright (c) 2025 johxyz

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Support

For questions, bugs, or feature requests, please open an issue in the repository. 