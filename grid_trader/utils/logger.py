# grid_trader/utils/logger.py
import logging
import sys
# Use a conditional import for the __main__ block or direct execution context
if __name__ == '__main__':
    # In __main__, __package__ might be None or not what's expected for relative imports
    # Try to import config in a way that works if script is run directly from package root
    try:
        from .. import config as main_config
    except (ImportError, ValueError): # ValueError for attempted relative import in non-package
        # Fallback for direct script execution if relative import fails
        # This assumes config.py is in the same directory or accessible via sys.path
        # For robust testing, a proper test setup or PYTHONPATH adjustment is needed.
        class DummyConfigMain:
            LOG_LEVEL = "INFO"
            LOG_FILE = "main_logger_test.log"
            # Add any other config attributes get_logger might access
        main_config = DummyConfigMain()
        print(f"Warning: Could not import '..config' directly in __main__. Using dummy: {main_config.LOG_FILE}")
else:
    from .. import config # Standard import for when logger.py is part of the package

def get_logger(name: str):
    """
    Creates and configures a logger instance.
    The logger's level and output file are determined by settings in config.py.
    """
    logger = logging.getLogger(name)

    # Prevent multiple handlers if logger already configured (e.g., in Jupyter)
    if logger.hasHandlers():
        logger.handlers.clear()

    # Set level from config
    log_level_str = getattr(config, 'LOG_LEVEL', 'INFO').upper()
    numeric_level = getattr(logging, log_level_str, logging.INFO)
    logger.setLevel(numeric_level)

    # Create formatter
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # Create console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(numeric_level)
    ch.setFormatter(formatter)
    logger.addHandler(ch)

    # Create file handler if LOG_FILE is specified in config
    log_file = getattr(config, 'LOG_FILE', None)
    if log_file:
        try:
            fh = logging.FileHandler(log_file, mode='a') # Append mode
            fh.setLevel(numeric_level)
            fh.setFormatter(formatter)
            logger.addHandler(fh)
        except Exception as e:
            logger.error(f"Failed to create file handler for {log_file}: {e}", exc_info=True)
            # Still want to log to console if file logging fails
            pass


    logger.propagate = False # Prevent logging from propagating to the root logger

    return logger

# Example of how to use the logger (optional, for demonstration)
if __name__ == '__main__':
    # In the __main__ block, 'config' is now 'main_config' (either real or dummy)
    # The get_logger function itself will use the 'config' from its module scope,
    # which is correctly imported using 'from .. import config' when logger.py is not __main__.

    _config_to_use_in_main = main_config

    # Override LOG_FILE for the example run to avoid writing to the default log file
    # if we are using a real config. If dummy, it already has its own log file.
    _original_log_file_main = getattr(_config_to_use_in_main, 'LOG_FILE', "default_main_test.log")
    _example_log_file_main = "logger_main_example.log"
    _original_log_level_main = getattr(_config_to_use_in_main, 'LOG_LEVEL', "INFO")

    # Temporarily modify the attributes of the config object (real or dummy)
    # Note: get_logger will use the 'config' object from its own module scope.
    # If we are in __main__ and using a dummy 'main_config', this won't affect
    # the 'config' that get_logger uses unless get_logger is also modified to see 'main_config'.
    # This __main__ block is primarily for testing the logger's behavior with a known config.
    # For this to reliably test get_logger, get_logger needs to see these temporary changes.
    # One way is to pass config to get_logger, or rely on 'main_config' being the same object
    # if the conditional import logic makes 'config' and 'main_config' point to the same thing.

    # Let's assume 'config' within get_logger refers to 'main_config' due to the conditional import at the top.
    # This is only true if the first try block in conditional import was successful.
    # If DummyConfigMain was used, then 'config' in get_logger (from 'from .. import config') is NOT main_config.
    # This __main__ test logic is getting complicated.

    # Simplification for __main__: Use a fixed config for testing __main__ execution.
    # The goal is to test if get_logger *can* work.

    # Let's redefine 'config' for the scope of this __main__ block for simplicity of test
    class MainTestConfig:
        LOG_LEVEL = "DEBUG"
        LOG_FILE = "logger_main_test_output.log"

    # Monkeypatch the 'config' that get_logger will see IF it's using the 'else' branch of conditional import.
    # This is fragile. A better way is to pass config into get_logger or use a test runner.
    if 'config' in globals(): # If 'from .. import config' in the 'else' branch was hit (it wasn't, we are in __main__)
        pass # This won't help here.

    # The 'get_logger' function, as written, will try to use 'config' from its module scope.
    # If __name__ == '__main__', the 'from .. import config' in the 'else' branch is NOT executed.
    # So, 'config' (that get_logger will try to use) is not defined in this specific scenario.
    # This needs a fix in get_logger or how config is provided to it.

    # Simplest fix: make get_logger accept an optional config object,
    # or ensure 'config' is always defined in its scope.

    # Let's assume for __main__ that we redefine 'config' globally for this test.
    # This is a hack for testing.
    global config
    config = MainTestConfig()
    print(f"Running __main__ example with forced config: {config.LOG_LEVEL}, {config.LOG_FILE}")


    logger_main = get_logger(__name__) # __name__ will be "__main__"
    logger_main.debug(f"This is a debug message from __main__ using {config.LOG_FILE}.")
    logger_main.info(f"This is an info message from __main__ using {config.LOG_FILE}.")
    logger_main.warning(f"This is a warning message from __main__ using {config.LOG_FILE}.")

    logger_module_test = get_logger("test_module")
    logger_module_test.info("Info from test_module.")
    logger_module_test.debug("Debug from test_module.")

    print(f"Example log messages sent to console and '{config.LOG_FILE}'.")
    print("Note: This __main__ block uses a simplified/hacked config for direct execution testing.")
    print("The `from .. import config` is for package usage.")
