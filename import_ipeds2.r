# Load required libraries
library(DBI)
library(duckdb)
library(RODBC)
library(whisker)
library(rvest)
library(httr)
library(xml2)
library(stringr)
library(zip)

LOAD_PROVISIONAL <- TRUE
LOAD_NUM_YEARS <- 0  # 5 # Set to 0 or less to load all years
SAVE_DB_FILES <- TRUE

# Set EXECUTE_REMOTE to TRUE if running locally (not in a browser session)
is_local <- interactive() && Sys.getenv("R_PLATFORM") != "wasm32-unknown-emscripten" && Sys.getenv("SHINY_PORT") == ""
EXECUTE_REMOTE <- is_local
#EXECUTE_REMOTE <- TRUE

# https://nces.ed.gov/ipeds/use-the-data/download-access-database
IPEDS_ROOT_URL <- "https://nces.ed.gov"
IPEDS_ACCESS_DBS_URL <- str_glue("{IPEDS_ROOT_URL}/ipeds/use-the-data/download-access-database")

log_message <- function(message) {
  cat(sprintf("[%s] %s\n", format(Sys.time(), "%Y-%m-%d %H:%M:%S"), message))
}

# Define paths
data_folder <- "data"
output_folder <- "output"

# Ensure output folder exists
if (!dir.exists(output_folder)) {
    dir.create(output_folder)
}

# Set update flag
update_flag <- TRUE  # Change this to FALSE if you do not want to update existing tables

# Create a temporary folder for the Access database files
temp_folder <- tempdir()
if (!dir.exists(temp_folder)) {
    dir.create(temp_folder)
}
# Download the Access database files if they are not already present

# Get the list of Access database files from the website. They are stored in 
# a table with columns "Database Name" (actually a zip file), "Documentation" (an Excel file),
# "Release Type" (Provisional or Final), and "Release Date" (Month Year).
# The files are listed in chronological order, so the most recent files are at the bottom.

# The files are named like IPEDS_2023-24_Provisional.zip, IPEDS_2022-23_Final.zip, etc.
# The zip file contains a folder (same as the base name) with the database files named like
# IPEDS202324.accdb, IPEDS202223.accdb, etc. Ignore the other files in the folder.
# Generate code to download the files and extract the database files into the 
# temporary folder temp_folder.

# Scrape the IPEDS Access DB download page
log_message("Scraping IPEDS Access DB download page...")
page <- read_html(IPEDS_ACCESS_DBS_URL)
tbl <- html_node(page, "table")
db_info <- html_table(tbl, fill = TRUE)

# Clean column names
names(db_info) <- tolower(gsub("[^a-zA-Z0-9]", "_", names(db_info)))

# Extract all <a> nodes from the first column (Database Name)
db_links <- html_nodes(tbl, xpath = ".//tr/td[1]/a")
db_info$zip_url <- html_attr(db_links, "href")
db_info$zip_url <- paste0(IPEDS_ROOT_URL, db_info$zip_url)
db_info$database_name <- basename(db_info$zip_url)

# Filter for zip files (Database Name column)
db_info <- db_info[grepl("\\.zip$", db_info$database_name), ]

# Optionally filter for Provisional/Final
if (LOAD_PROVISIONAL) {
    db_info <- db_info[grepl("Provisional|Final", db_info$release_type, ignore.case = TRUE), ]
} else {
    db_info <- db_info[grepl("Final", db_info$release_type, ignore.case = TRUE), ]
}

# Keep only the most recent N years
if (LOAD_NUM_YEARS <= 0) {
    log_message("LOAD_NUM_YEARS is set to 0 or less, skipping year filtering.")
} else if (LOAD_NUM_YEARS > nrow(db_info)) {
    log_message(str_glue("LOAD_NUM_YEARS is set to {LOAD_NUM_YEARS}, but only {nrow(db_info)} years are available. Using all available years."))
} else {
    log_message(str_glue("Filtering to the most recent {LOAD_NUM_YEARS} years."))
    db_info <- tail(db_info, LOAD_NUM_YEARS)
}

# Extract years from the database_name, e.g., IPEDS_2023-24_Provisional.zip
get_start_year <- function(name) {
    m <- regmatches(name, regexec("([0-9]{4})", name))
    if (length(m[[1]]) > 1) return(m[[1]][2])
    return(NA)
}
years <- sapply(db_info$database_name, get_start_year)
years <- as.integer(years[!is.na(years)])
first_year <- min(years)
last_year <- max(years)
duckdb_file <- file.path(output_folder, str_glue("ipeds_{first_year}-{last_year}.duckdb"))

if (!SAVE_DB_FILES) {
    data_folder <- temp_folder
}
log_message(str_glue("Temporary directory: {temp_folder}"))
log_message(str_glue("Database files will be saved to: {data_folder}"))
log_message(str_glue("DuckDB file: {duckdb_file}"))

# Connect to DuckDB
duckdb_conn <- dbConnect(duckdb::duckdb(), dbdir = duckdb_file, config = list("allow_unsigned_extensions" = "TRUE"))

if (EXECUTE_REMOTE) {
    # This will build a string of queries to execute later
    load_nanodbc <- "install nanodbc from community;load nanodbc;"
} else {
    load_nanodbc <- "install nanodbc;load nanodbc;"
    # dbExecute(duckdb_conn, "SET allow_unsigned_extensions = TRUE;")
    rc <- dbExecute(duckdb_conn, load_nanodbc)
}

# Download and extract .accdb files
for (i in seq_len(nrow(db_info))) {
    # zip_url <- html_node(read_html(IPEDS_ACCESS_DBS_URL), xpath = sprintf("//a[contains(text(), '%s')]", db_info$database_name[i])) %>%
    #     html_attr("href")
    # # If the link is relative, prepend the base URL
    # if (!grepl("^https?://", zip_url)) {
    #     zip_url <- paste0("https://nces.ed.gov", zip_url)
    # }
    zip_url <- db_info$zip_url[i]
    zip_file <- file.path(temp_folder, db_info$database_name[i])
    accdb_pattern <- paste0("IPEDS[0-9]{6}\\.accdb$")

    # Download zip if not already present
    if (!file.exists(zip_file)) {
        log_message(paste("Downloading", zip_url))
        GET(zip_url, write_disk(zip_file, overwrite = TRUE))
    } else {
        log_message(paste("Already downloaded", basename(zip_file)))
    }

    # List files in zip
    zip_list <- utils::unzip(zip_file, list = TRUE)
    accdb_file <- zip_list$Name[grepl(accdb_pattern, basename(zip_list$Name))]
    if (length(accdb_file) == 0) {
        log_message(paste("No .accdb files found in", basename(zip_file)))
        next
    }
    # Extract only .accdb files
    log_message(str_glue("Extracting {basename(accdb_file)} from {basename(zip_file)}"))
    extracted_file <- utils::unzip(zip_file, files = accdb_file, exdir = data_folder, overwrite = TRUE)
    
    # if the extracted file contains a directory, move the file up into this folder
    # and delete the directory
    extracted_basename <- basename(extracted_file)
    extracted_dir <- dirname(extracted_file)
    if (normalizePath(extracted_dir) != normalizePath(data_folder)) {
        dest_file <- file.path(data_folder, extracted_basename)
        if (!file.exists(dest_file)) {
            Path_rename(extracted_file, dest_file)
        }
        # Remove the now-empty directory if possible
        if (length(list.files(extracted_dir)) == 0) {
            #unlink(extracted_dir, recursive = TRUE)
            Path_rmdir(extracted_dir)
        }
        extracted_file <- dest_file
    }
    
    
    # delete the zip file if the accdb files were extracted
    if (file.exists(extracted_file)) {
        log_message(paste("Deleting zip file", basename(zip_file)))
    #     file.remove(zip_file)
    }
}


# Get list of Access database files
access_files <- list.files(data_folder, pattern = "IPEDS.*\\.accdb$", full.names = TRUE)

if (EXECUTE_REMOTE) {
    duckdb_queries <- load_nanodbc
}

# Loop through each Access database
for (access_file in access_files) {
    log_message(paste("Processing", basename(access_file)))

    # File names look like IPEDS201920.accdb, IPEDS202021.accdb, etc.
    # Parse the year from the file name
    year <- sub("IPEDS([0-9]{4}).*", "\\1", basename(access_file))
    # Parse the year to get the last two digits
    year2 <- substr(year, 3, 4)

    access_conn <- odbcConnectAccess2007(access_file)
    all_tables_qry <- paste0("SELECT DISTINCT TableName FROM Tables", year2, " WHERE TableName NOT IN ('efxxxxcp','NA')")
    all_tables <- sqlQuery(access_conn, all_tables_qry)
    # The Tables## registry omits the metadata tables; add them explicitly
    meta_tables <- c(paste0("Tables", year2), paste0("vartable", year2), paste0("valuesets", year2))
    meta_tables <- meta_tables[tolower(meta_tables) %in% tolower(sqlTables(access_conn)$TABLE_NAME)]
    all_tables <- rbind(all_tables, data.frame(TableName = meta_tables))
    close(access_conn)
    
    for (table_name in all_tables$TableName) {
        log_message(paste("Processing table", table_name))

        # Check if the table already exists in DuckDB
        table_exists <- dbExistsTable(duckdb_conn, table_name)
        if ( table_exists && !update_flag) {
            log_message(paste("Table", table_name, "already exists. Skipping."))
            next
        } else {
            if (table_exists) {
                log_message(paste("Table", table_name, "already exists. Dropping."))
                dbRemoveTable(duckdb_conn, table_name)
            }
        }

        whisker_vars <- list(
            file_name = access_file,
            table_name = tolower(table_name)
        )

        # For now, need to create a file of query commands and run them at the end on the command line
        query_template <- "create table {{table_name}} as from odbc_scan(table_name={{table_name}},connection='Driver={Microsoft Access Driver (*.mdb, *.accdb)};Dbq={{file_name}};',encoding='WINDOWS-1252');"
        query <- whisker.render(query_template, data = whisker_vars)

        if (EXECUTE_REMOTE) {
            log_message(str_glue("Adding query for table {table_name}"))
            duckdb_queries <- paste(duckdb_queries, query, sep = "\n")
        } else {
            # Execute the query
            log_message(str_glue("Executing query for table {table_name}"))
            dbExecute(duckdb_conn, query)
        }

        #log_message(paste("Table", table_name, "created."))

        # Check if the table has been created successfully
        #if (!dbExistsTable(duckdb_conn, table_name)) {
        #    log_message(paste("Failed to create table", table_name))
        #    next
        #}

    }
}


log_message("All tables processed. Closing database connection.")
# first, close the duckdb databsae
dbDisconnect(duckdb_conn, shutdown = TRUE)


if (EXECUTE_REMOTE) {
    # Execute all queries at once
    log_message("Executing all queries at once.")

    # then save the queries to a local file
    queries_file <- file.path(output_folder, "queries.sql")
    writeLines(duckdb_queries, queries_file)
    # then run the queries from the command line
    cmd <- str_glue("duckdb -c \".read {queries_file}\" {duckdb_file}")
    system(cmd)
}

