import sqlite3
import tkinter as tk
from tkinter import ttk, filedialog

class LogViewerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Log Viewer")
        self.create_widgets()

    def create_widgets(self):
        # Create a frame to hold the treeview and scrollbars
        tree_frame = tk.Frame(self.root)
        tree_frame.pack(fill="both", expand=True, padx=5, pady=5)

        # Create the treeview
        self.tree = ttk.Treeview(tree_frame, columns=("timestamp", "step", "source", "message"), show="headings")
        self.tree.heading("timestamp", text="Timestamp")
        self.tree.heading("step", text="Step")
        self.tree.heading("source", text="Source")
        self.tree.heading("message", text="Message")
        
        # Set column widths and configure for better message display
        self.tree.column("timestamp", width=150, minwidth=100)
        self.tree.column("step", width=80, minwidth=50)
        self.tree.column("source", width=100, minwidth=80)
        self.tree.column("message", width=400, minwidth=200)
        
        # Configure columns to stretch
        self.tree.column("timestamp", stretch=True)
        self.tree.column("step", stretch=True)
        self.tree.column("source", stretch=True)
        self.tree.column("message", stretch=True)

        # Create vertical scrollbar
        v_scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=v_scrollbar.set)

        # Create horizontal scrollbar
        h_scrollbar = ttk.Scrollbar(tree_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(xscrollcommand=h_scrollbar.set)

        # Grid layout for treeview and scrollbars
        self.tree.grid(row=0, column=0, sticky="nsew")
        v_scrollbar.grid(row=0, column=1, sticky="ns")
        h_scrollbar.grid(row=1, column=0, sticky="ew")

        # Configure grid weights for proper resizing
        tree_frame.grid_rowconfigure(0, weight=1)
        tree_frame.grid_columnconfigure(0, weight=1)

        # Button frame
        btn_frame = tk.Frame(self.root)
        btn_frame.pack(fill="x")

        open_btn = tk.Button(btn_frame, text="Open Log DB", command=self.open_log_db)
        open_btn.pack(side="left", padx=5, pady=5)
        
        clear_btn = tk.Button(btn_frame, text="Clear Data", command=self.clear_data)
        clear_btn.pack(side="left", padx=5, pady=5)
        
        # Store current database path
        self.current_db_path = None
        
        # Add double-click handler to show full message
        self.tree.bind("<Double-1>", self.on_double_click)

    def on_double_click(self, event):
        """Show full message content in a popup window when double-clicked"""
        item = self.tree.selection()[0]
        if item:
            values = self.tree.item(item, "values")
            if len(values) >= 4:
                # Create popup window
                popup = tk.Toplevel(self.root)
                popup.title("Full Message")
                popup.geometry("600x400")
                
                # Create text widget with scrollbar
                text_frame = tk.Frame(popup)
                text_frame.pack(fill="both", expand=True, padx=10, pady=10)
                
                text_widget = tk.Text(text_frame, wrap="word", font=("Consolas", 10))
                text_scrollbar = ttk.Scrollbar(text_frame, orient="vertical", command=text_widget.yview)
                text_widget.configure(yscrollcommand=text_scrollbar.set)
                
                # Display message details
                message_content = f"Timestamp: {values[0]}\nStep: {values[1]}\nSource: {values[2]}\n\nMessage:\n{values[3]}"
                text_widget.insert("1.0", message_content)
                text_widget.config(state="disabled")
                
                # Pack text widget and scrollbar
                text_widget.pack(side="left", fill="both", expand=True)
                text_scrollbar.pack(side="right", fill="y")
                
                # Close button
                close_btn = tk.Button(popup, text="Close", command=popup.destroy)
                close_btn.pack(pady=5)

    def clear_data(self):
        """Clear all data from the database and refresh the display"""
        if not self.current_db_path:
            print("No database loaded. Please open a database first.")
            return
            
        try:
            # Ask for confirmation
            import tkinter.messagebox as msgbox
            result = msgbox.askyesno("Confirm Clear", 
                                   "Are you sure you want to delete ALL data from the database?\n\n"
                                   "This action cannot be undone!")
            
            if not result:
                return
            
            # Delete all data from database
            conn = sqlite3.connect(self.current_db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM logs")
            conn.commit()
            rows_deleted = cursor.rowcount
            conn.close()
            
            # Clear the treeview display
            for row in self.tree.get_children():
                self.tree.delete(row)
            
            print(f"Successfully deleted {rows_deleted} records from database and cleared display")
            
        except Exception as e:
            print(f"Error clearing database: {e}")
            import tkinter.messagebox as msgbox
            msgbox.showerror("Error", f"Failed to clear database:\n{str(e)}")

    def open_log_db(self):
        file_path = filedialog.askopenfilename(filetypes=[("SQLite DB", "*.db")])
        if not file_path:
            return

        # Store the current database path
        self.current_db_path = file_path

        conn = sqlite3.connect(file_path)
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT timestamp, step, source, message FROM logs ORDER BY timestamp DESC")
            rows = cursor.fetchall()
            for row in self.tree.get_children():
                self.tree.delete(row)
            for row in rows:
                self.tree.insert("", "end", values=row)
        except Exception as e:
            print("Error reading logs:", e)
        finally:
            conn.close()

if __name__ == "__main__":
    root = tk.Tk()
    app = LogViewerApp(root)
    root.mainloop()