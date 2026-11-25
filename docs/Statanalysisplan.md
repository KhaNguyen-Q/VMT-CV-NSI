The existing Export Report Table workflow is isolated from the statistical analysis pipeline and should be integrated with a aggregated baseline system. 

Rather than comparing a single Baseline.csv against a single Comparison.csv, the report-generation process should use the "outputRanges"/"comparator_config.json". The reason why it's either or is because I have not thought of the true implementation yet so we will brain storm which is the true comparator "acceptable range" 

 

This is how it behaves currently: 

Takes the two loaded CSV files in compare_window.py (Baseline.csv, Comparison.csv)  

Click the "Export report table..." 

Runs the script that "exports a comparison table report" using the two CSV files 

Comparison is completed with just those 2 files. 

Outputs a .docx file which can be edited 

Recommended changes: 

Rewire the script that "exports a comparison table report" to use the "Analyze exported range CSVs..." Flow to create a mass aggregated "Baseline" so the loaded comparison.csv are compared to a dynamic "Baseline" that includes multiple samples. 

 

The intended new behaviour should: 

After the user Completes "Run all Analysis" in the "Range Statistical analysis window". The "outputRanges" files and or the "comparator_config.json" should be stored somewhere that they can be referenced by the new comparison script (The user can select them) 

Takes the loaded Comparison.csv (making sure it labeled as 1 of the 3 treatment profiles) 

Select some sort of "which treatment profile is this", the result points to the appropriate stored "outputRanges" files and or the "comparator_config.json" 

Runs a script that acts as "exports a comparison table" (The name of the button can be changed) using the Comparison.csv and (outputRanges/comparator_config.json) 

Outputs the .docx file which can be edited 

 

The only changes are the prerequisite steps before the "export table report". When "export table report" is clicked, it should check if there is a comparison.csv loaded and prompt the user. 

Treatment profile options 

Depending on what Treatment profile is selected it will try to find the referenced "outputRanges.csv"/"comparator_config.json" 

The user can change the referenced "outputRanges.csv"/"comparator_config.json" but first it is defaulted 

Then generate the .docx 

 

The compare_window/py essentially becomes a .csv processor component and the actual comparison is completed with the "Export report table" logic